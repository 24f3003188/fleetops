"""
FleetOps — ETS2 Observability Engine
Main entry point: bootstraps OpenTelemetry, polls the ETS2 telemetry API,
and feeds data into SigNoz via OTLP gRPC.

Usage:
    python -m ets2_fleetops.main
"""

import logging
import signal
import sys
import time

import requests
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

from ets2_fleetops.metrics import create_instruments, record_metrics
from ets2_fleetops.traces import JobTracer
from ets2_fleetops.events import EventDetector

# ─── Configuration ───────────────────────────────────────────────────────────

SIGNOZ_OTLP_ENDPOINT = "http://127.0.0.1:4318"
ETS2_TELEMETRY_URL = "http://localhost:25555/api/ets2/telemetry"
POLL_INTERVAL_SECONDS = 0.5  # 2 polls per second
SERVICE_NAME = "fleetops-ets2"

# ─── OTel Bootstrap ─────────────────────────────────────────────────────────


def bootstrap_otel() -> tuple:
    """Initialize all three OTel signal providers."""

    resource = Resource.create(
        {
            "service.name": SERVICE_NAME,
            "service.version": "1.0.0",
            "deployment.environment": "hackathon",
        }
    )

    # Metrics
    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=f"{SIGNOZ_OTLP_ENDPOINT}/v1/metrics"),
        export_interval_millis=1000,
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)
    meter = metrics.get_meter("fleetops.ets2", "1.0.0")

    # Traces
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{SIGNOZ_OTLP_ENDPOINT}/v1/traces")
        )
    )
    trace.set_tracer_provider(tracer_provider)
    tracer = trace.get_tracer("fleetops.ets2", "1.0.0")

    # Logs
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(
            OTLPLogExporter(endpoint=f"{SIGNOZ_OTLP_ENDPOINT}/v1/logs")
        )
    )
    handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    logging.getLogger("fleetops.events").addHandler(handler)
    logging.getLogger("fleetops.events").setLevel(logging.INFO)

    return meter, tracer, meter_provider, tracer_provider, logger_provider


# ─── Telemetry Polling ───────────────────────────────────────────────────────


def fetch_telemetry() -> dict | None:
    """Fetch latest telemetry JSON from the funbit server."""
    try:
        resp = requests.get(ETS2_TELEMETRY_URL, timeout=2)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        return None
    except Exception as e:
        print(f"  [!] Fetch error: {e}")
        return None


def build_attributes(data: dict) -> dict:
    """Build resource-level attributes from static truck/game/job info."""
    truck = data.get("truck", {})
    game = data.get("game", {})
    job = data.get("job", {})

    source = job.get("sourceCity", "")
    dest = job.get("destinationCity", "")
    job_route = f"{source}->{dest}" if source and dest else "no-job"

    return {
        "truck.make": truck.get("make", "Unknown"),
        "truck.model": truck.get("model", "Unknown"),
        "truck.id": truck.get("id", "unknown"),
        "game.name": game.get("gameName", "ETS2"),
        "job.route": job_route,
    }


# ─── Main Loop ───────────────────────────────────────────────────────────────

running = True


def signal_handler(sig, frame):
    global running
    print("\n[!] Shutting down FleetOps...")
    running = False


def main():
    global running
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("=" * 60)
    print("  [FleetOps] ETS2 Observability Engine")
    print("=" * 60)
    print(f"  SigNoz OTLP endpoint: {SIGNOZ_OTLP_ENDPOINT}")
    print(f"  ETS2 Telemetry URL:   {ETS2_TELEMETRY_URL}")
    print(f"  Poll interval:        {POLL_INTERVAL_SECONDS}s")
    print("=" * 60)

    # Bootstrap OpenTelemetry
    print("  Initializing OpenTelemetry...")
    meter, tracer, meter_provider, tracer_provider, logger_provider = bootstrap_otel()

    # Create metric instruments
    instruments = create_instruments(meter)
    print(f"  [OK] Created {len(instruments)} metric instruments")

    # Create job tracer & event detector
    job_tracer = JobTracer(tracer)
    event_detector = EventDetector()
    print("  [OK] Job tracer and event detector ready")

    print("\n  Waiting for ETS2 telemetry server...")

    poll_count = 0
    connected = False

    while running:
        data = fetch_telemetry()

        if data is None:
            if connected:
                print("  [!] Lost connection to ETS2 telemetry server")
                connected = False
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        game = data.get("game", {})
        if not game.get("connected", False):
            if connected:
                print("  [pause] Game not connected (menu/loading)")
                connected = False
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        if game.get("paused", False):
            if connected:
                print("  [pause] Game is paused, suspending telemetry stream")
                connected = False
            
            # The game pauses when the Delivery Results screen appears!
            # We MUST still update the job tracer so it detects the job ended.
            attributes = build_attributes(data)
            job_tracer.update(data, attributes)
            
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        if not connected:
            truck = data.get("truck", {})
            print(
                f"  [OK] Connected! Truck: {truck.get('make', '?')} {truck.get('model', '?')}"
            )
            connected = True

        # Build attributes
        attributes = build_attributes(data)

        # Record metrics
        record_metrics(instruments, data, attributes)

        # Update job tracer (trace state machine)
        job_tracer.update(data, attributes)

        # Check for events (logs) and add them to the job trace
        detected_events = event_detector.check(data, attributes)
        if detected_events:
            job_tracer.add_events(detected_events)

        # Console output every 10 polls (~5 seconds)
        poll_count += 1
        if poll_count % 10 == 0:
            truck = data.get("truck", {})
            job = data.get("job", {})
            nav = data.get("navigation", {})
            speed = abs(truck.get("speed", 0))
            rpm = truck.get("engineRpm", 0)
            fuel = truck.get("fuel", 0)
            gear = truck.get("displayedGear", 0)
            src = job.get("sourceCity", "-")
            dst = job.get("destinationCity", "-")
            dist = nav.get("estimatedDistance", 0) / 1000  # to km

            print(
                f"  [>] {speed:5.0f} km/h | RPM {rpm:6.0f} | G{gear:2d} | "
                f"Fuel {fuel:6.0f}L | Job: {src}->{dst} ({dist:.0f}km)"
            )
            
            # Calculate total truck wear for component degradation analysis
            engine_wear = truck.get("wearEngine", 0)
            trans_wear = truck.get("wearTransmission", 0)
            chassis_wear = truck.get("wearChassis", 0)
            total_damage = engine_wear + trans_wear + chassis_wear
            
            # Predict SLA breaches (Required Average Speed)
            req_speed = 0
            eta_str = job.get("remainingTime", "")
            from ets2_fleetops.metrics import parse_ets2_time
            sec_rem = parse_ets2_time(eta_str)
            if sec_rem > 0:
                # distance in m, time in sec -> m/s. Multiply by 3.6 for km/h
                req_speed = (nav.get("estimatedDistance", 0) / sec_rem) * 3.6
                
            # Detect Idling (Engine on, speed < 1 km/h)
            is_idling = 1 if (truck.get("engineOn", False) and speed < 1.0) else 0
            
            # Emit high-resolution telemetry log to bypass Metric UI bugs!
            placement = truck.get("placement", {})
            acceleration = truck.get("acceleration", {})
            
            logging.getLogger("fleetops.events").info(
                "telemetry_tick",
                extra={
                    "speed": float(speed),
                    "speed_limit": float(nav.get("speedLimit", 0)),
                    "required_speed": float(req_speed),
                    "is_idling": float(is_idling),
                    "rpm": float(rpm),
                    "fuel": float(fuel),
                    "gear": float(gear),
                    "distance": float(dist),
                    "cruise_control": float(1 if truck.get("cruiseControlOn", False) else 0),
                    "retarder": float(truck.get("retarderBrake", 0)),
                    "total_damage": float(total_damage),
                    "placement_x": float(placement.get("x", 0)),
                    "placement_y": float(placement.get("y", 0)),
                    "placement_z": float(placement.get("z", 0)),
                    "heading": float(placement.get("heading", 0)),
                    "pitch": float(placement.get("pitch", 0)),
                    "acceleration_x": float(acceleration.get("x", 0)),
                    "acceleration_y": float(acceleration.get("y", 0)),
                    "acceleration_z": float(acceleration.get("z", 0)),
                    "wear_engine": float(engine_wear),
                    "wear_transmission": float(trans_wear),
                    "wear_chassis": float(chassis_wear),
                    "wear_wheels": float(truck.get("wearWheels", 0)),
                    **attributes
                }
            )

        time.sleep(POLL_INTERVAL_SECONDS)

    # Graceful shutdown
    print("  Shutting down OTel providers...")
    job_tracer.shutdown(data if 'data' in locals() and data else None)
    meter_provider.shutdown()
    tracer_provider.shutdown()
    logger_provider.shutdown()
    print("  [OK] FleetOps stopped cleanly.")


if __name__ == "__main__":
    main()
