"""
FleetOps — ETS2 Observability Engine
Traces module: models delivery jobs as distributed traces with spans.
"""

import time
from opentelemetry import trace


class JobTracer:
    """
    State machine that detects job start/end and creates OTel traces.

    A "Trace" = one complete delivery job (Linz → Salzburg).
    The root span covers the entire delivery duration.
    """

    def __init__(self, tracer: trace.Tracer):
        self.tracer = tracer
        self._current_span = None
        self._current_ctx = None
        self._current_job_key = None
        self._current_job_key = None
        self._job_start_time = None
        self._job_fuel_start = 0

    def _get_job_key(self, data: dict) -> str | None:
        """Create a unique key for the current job, or None if no job."""
        job = data.get("job", {})

        source = job.get("sourceCity", "")
        dest = job.get("destinationCity", "")
        income = job.get("income", 0)

        # A job is only active if cities exist and expected income > 0
        if not source or not dest or income <= 0:
            return None

        return f"{source}->{dest}"

    def update(self, data: dict, attributes: dict):
        """Called every poll cycle. Detects job transitions."""

        job_key = self._get_job_key(data)

        # Case 1: New job detected (or job changed)
        if job_key and job_key != self._current_job_key:
            # End previous job if one was active
            self._end_current_job(data)

            # Start new job trace
            self._start_new_job(data, job_key, attributes)

        # Case 2: Job ended (trailer detached or cities cleared)
        elif not job_key and self._current_job_key:
            self._end_current_job(data)

        # Case 3: Job still active — update span with live data
        elif self._current_span and self._current_span.is_recording():
            truck = data.get("truck", {})
            self._current_span.set_attribute(
                "ets2.current_speed", abs(truck.get("speed", 0))
            )
            self._current_span.set_attribute(
                "ets2.current_fuel", truck.get("fuel", 0)
            )

    def add_events(self, events: list[dict]):
        """Add child span events to the currently active job trace."""
        if self._current_span and self._current_span.is_recording():
            for evt in events:
                evt_name = evt.pop("event.name", "unknown_event")
                self._current_span.add_event(evt_name, attributes=evt)

    def _start_new_job(self, data: dict, job_key: str, attributes: dict):
        """Start a new trace for a delivery job."""
        job = data.get("job", {})
        trailer = data.get("trailer", {})
        truck = data.get("truck", {})
        nav = data.get("navigation", {})

        source_city = job.get("sourceCity", "Unknown")
        dest_city = job.get("destinationCity", "Unknown")
        span_name = f"Delivery: {source_city} -> {dest_city}"

        self._current_ctx = trace.set_span_in_context(trace.INVALID_SPAN)
        self._current_span = self.tracer.start_span(
            name=span_name,
            attributes={
                **attributes,
                "ets2.job.source_city": source_city,
                "ets2.job.source_company": job.get("sourceCompany", ""),
                "ets2.job.destination_city": dest_city,
                "ets2.job.destination_company": job.get("destinationCompany", ""),
                "ets2.job.income": float(job.get("income", 0)),
                "ets2.job.deadline": job.get("deadlineTime", ""),
                "ets2.trailer.name": trailer.get("name", ""),
                "ets2.trailer.mass_kg": float(trailer.get("mass", 0)),
                "ets2.truck.fuel_at_start": float(truck.get("fuel", 0)),
                "ets2.truck.odometer_at_start": float(truck.get("odometer", 0)),
                "ets2.nav.distance_at_start": float(nav.get("estimatedDistance", 0)),
            },
        )
        self._current_job_key = job_key
        self._job_start_time = time.time()
        self._job_fuel_start = truck.get("fuel", 0)
        self._job_odometer_start = truck.get("odometer", 0)
        self._job_nav_distance_start = nav.get("estimatedDistance", 0)

        print(f"  [+] TRACE STARTED: {span_name}")

    def _end_current_job(self, data: dict):
        """End the current job trace."""
        if self._current_span and self._current_span.is_recording():
            truck = data.get("truck", {})
            duration = time.time() - self._job_start_time if self._job_start_time else 0

            self._current_span.set_attribute("ets2.job.duration_seconds", duration)
            self._current_span.set_attribute(
                "ets2.truck.fuel_at_end", truck.get("fuel", 0)
            )

            # Calculate fuel used during this job
            fuel_end = truck.get("fuel", 0)
            if self._job_fuel_start and fuel_end:
                self._current_span.set_attribute(
                    "ets2.job.fuel_used", self._job_fuel_start - fuel_end
                )
                
            # Calculate route efficiency (wasted mileage)
            odometer_end = truck.get("odometer", 0)
            if getattr(self, "_job_odometer_start", 0) and odometer_end:
                actual_distance_km = odometer_end - self._job_odometer_start
                planned_distance_km = getattr(self, "_job_nav_distance_start", 0) / 1000.0
                
                if planned_distance_km > 0:
                    wasted_mileage = actual_distance_km - planned_distance_km
                    self._current_span.set_attribute("ets2.job.actual_distance_km", actual_distance_km)
                    self._current_span.set_attribute("ets2.job.planned_distance_km", planned_distance_km)
                    self._current_span.set_attribute("ets2.job.wasted_mileage_km", wasted_mileage)

            self._current_span.set_status(trace.StatusCode.OK)
            self._current_span.end()

            print(f"  [-] TRACE ENDED: {self._current_job_key} ({duration:.0f}s)")

        self._current_span = None
        self._current_ctx = None
        self._current_job_key = None
        self._job_start_time = None

    def shutdown(self, last_data: dict | None = None):
        """Clean up any active span on shutdown, ensuring attributes are calculated."""
        if self._current_span and self._current_span.is_recording():
            if last_data:
                # Calculate metrics up to the point of shutdown
                truck = last_data.get("truck", {})
                duration = time.time() - self._job_start_time if self._job_start_time else 0
                fuel_end = truck.get("fuel", 0)
                
                self._current_span.set_attribute("ets2.job.duration_seconds", float(duration))
                self._current_span.set_attribute("ets2.truck.fuel_at_end", float(fuel_end))
                
                if self._job_fuel_start and fuel_end:
                    self._current_span.set_attribute(
                        "ets2.job.fuel_used", float(self._job_fuel_start - fuel_end)
                    )
                    
                odometer_end = truck.get("odometer", 0)
                if getattr(self, "_job_odometer_start", 0) and odometer_end:
                    actual_distance_km = odometer_end - self._job_odometer_start
                    planned_distance_km = getattr(self, "_job_nav_distance_start", 0) / 1000.0
                    wasted_mileage = actual_distance_km - planned_distance_km if planned_distance_km > 0 else 0.0
                    
                    self._current_span.set_attribute("ets2.job.actual_distance_km", float(actual_distance_km))
                    self._current_span.set_attribute("ets2.job.planned_distance_km", float(planned_distance_km))
                    self._current_span.set_attribute("ets2.job.wasted_mileage_km", float(wasted_mileage))
            
            self._current_span.set_status(trace.StatusCode.OK)
            self._current_span.end()
            print(f"  [-] TRACE ENDED ON SHUTDOWN: {self._current_job_key}")
            
        self._current_span = None
        self._current_ctx = None
        self._current_job_key = None
