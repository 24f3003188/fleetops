"""
FleetOps — ETS2 Observability Engine
Metrics module: defines all OpenTelemetry Gauge instruments for truck telemetry.
"""

from opentelemetry.metrics import Meter

def parse_ets2_time(time_str: str) -> int:
    """Parses ETS2 relative time string (0001-01-01T06:25:00Z) to total seconds."""
    if not time_str or len(time_str) < 19:
        return 0
    try:
        day = int(time_str[8:10])
        hour = int(time_str[11:13])
        minute = int(time_str[14:16])
        second = int(time_str[17:19])
        return (day - 1) * 86400 + hour * 3600 + minute * 60 + second
    except (ValueError, TypeError):
        return 0



def create_instruments(meter: Meter) -> dict:
    """Create all OTel gauge instruments and return them in a dict."""

    instruments = {}

    # --- Truck Performance ---
    instruments["speed"] = meter.create_gauge(
        name="ets2.truck.speed",
        description="Current truck speed",
        unit="km/h",
    )
    instruments["rpm"] = meter.create_gauge(
        name="ets2.truck.rpm",
        description="Engine RPM",
        unit="rpm",
    )
    instruments["gear"] = meter.create_gauge(
        name="ets2.truck.gear",
        description="Current displayed gear",
        unit="1",
    )
    instruments["throttle"] = meter.create_gauge(
        name="ets2.truck.throttle",
        description="Throttle pedal input (0-1)",
        unit="ratio",
    )
    instruments["brake"] = meter.create_gauge(
        name="ets2.truck.brake",
        description="Brake pedal input (0-1)",
        unit="ratio",
    )

    # --- Fuel & Fluids ---
    instruments["fuel"] = meter.create_gauge(
        name="ets2.truck.fuel",
        description="Current fuel level",
        unit="litres",
    )
    instruments["fuel_consumption"] = meter.create_gauge(
        name="ets2.truck.fuel_consumption",
        description="Average fuel consumption",
        unit="L/km",
    )
    instruments["oil_temp"] = meter.create_gauge(
        name="ets2.truck.oil_temp",
        description="Oil temperature",
        unit="Cel",
    )
    instruments["oil_pressure"] = meter.create_gauge(
        name="ets2.truck.oil_pressure",
        description="Oil pressure",
        unit="psi",
    )
    instruments["water_temp"] = meter.create_gauge(
        name="ets2.truck.water_temp",
        description="Water/coolant temperature",
        unit="Cel",
    )
    instruments["battery_voltage"] = meter.create_gauge(
        name="ets2.truck.battery_voltage",
        description="Battery voltage",
        unit="V",
    )
    instruments["adblue"] = meter.create_gauge(
        name="ets2.truck.adblue",
        description="AdBlue fluid level",
        unit="litres",
    )

    # --- Brakes & Pneumatics ---
    instruments["brake_temp"] = meter.create_gauge(
        name="ets2.truck.brake_temp",
        description="Brake disc temperature",
        unit="Cel",
    )
    instruments["air_pressure"] = meter.create_gauge(
        name="ets2.truck.air_pressure",
        description="Brake air tank pressure",
        unit="psi",
    )

    # --- Wear (0.0 = new, 1.0 = destroyed) ---
    instruments["wear_engine"] = meter.create_gauge(
        name="ets2.truck.wear.engine",
        description="Engine wear level",
        unit="ratio",
    )
    instruments["wear_transmission"] = meter.create_gauge(
        name="ets2.truck.wear.transmission",
        description="Transmission wear level",
        unit="ratio",
    )
    instruments["wear_cabin"] = meter.create_gauge(
        name="ets2.truck.wear.cabin",
        description="Cabin wear level",
        unit="ratio",
    )
    instruments["wear_chassis"] = meter.create_gauge(
        name="ets2.truck.wear.chassis",
        description="Chassis wear level",
        unit="ratio",
    )
    instruments["wear_wheels"] = meter.create_gauge(
        name="ets2.truck.wear.wheels",
        description="Wheels wear level",
        unit="ratio",
    )
    instruments["trailer_wear"] = meter.create_gauge(
        name="ets2.trailer.wear",
        description="Trailer wear/damage level",
        unit="ratio",
    )

    # --- Navigation ---
    instruments["nav_distance"] = meter.create_gauge(
        name="ets2.nav.distance_remaining",
        description="Estimated distance to destination",
        unit="m",
    )
    instruments["nav_speed_limit"] = meter.create_gauge(
        name="ets2.nav.speed_limit",
        description="Current road speed limit",
        unit="km/h",
    )

    # --- Odometer ---
    instruments["odometer"] = meter.create_gauge(
        name="ets2.truck.odometer",
        description="Truck odometer reading",
        unit="km",
    )

    # --- Logistics & Financials ---
    instruments["job_income"] = meter.create_gauge(
        name="ets2.job.income",
        description="Expected income for current job"
    )
    instruments["cargo_mass"] = meter.create_gauge(
        name="ets2.job.cargo_mass",
        description="Mass of the cargo",
        unit="kg",
    )
    instruments["job_remaining_time"] = meter.create_gauge(
        name="ets2.job.remaining_time_seconds",
        description="Estimated remaining time for delivery in seconds",
        unit="s",
    )

    # --- Driver Behavior ---
    instruments["cruise_control"] = meter.create_gauge(
        name="ets2.truck.cruise_control_on",
        description="Is cruise control active (1=on, 0=off)"
    )
    instruments["retarder"] = meter.create_gauge(
        name="ets2.truck.retarder_brake",
        description="Retarder brake level"
    )

    # --- Advanced Physics ---
    instruments["sway_angle"] = meter.create_gauge(
        name="ets2.trailer.sway_angle",
        description="Absolute angular difference between truck and trailer heading",
        unit="1",
    )
    instruments["g_force_x"] = meter.create_gauge(
        name="ets2.truck.g_force.x",
        description="Lateral acceleration (m/s^2)",
        unit="m/s2",
    )
    instruments["g_force_z"] = meter.create_gauge(
        name="ets2.truck.g_force.z",
        description="Longitudinal acceleration (m/s^2)",
        unit="m/s2",
    )

    return instruments


def record_metrics(instruments: dict, data: dict, attributes: dict):
    """Record all gauge values from a telemetry JSON snapshot."""

    truck = data.get("truck", {})
    trailer = data.get("trailer", {})
    nav = data.get("navigation", {})

    # Performance
    instruments["speed"].set(abs(truck.get("speed", 0)), attributes)
    instruments["rpm"].set(truck.get("engineRpm", 0), attributes)
    instruments["gear"].set(truck.get("displayedGear", 0), attributes)
    instruments["throttle"].set(truck.get("gameThrottle", 0), attributes)
    instruments["brake"].set(truck.get("gameBrake", 0), attributes)

    # Fuel & Fluids
    instruments["fuel"].set(truck.get("fuel", 0), attributes)
    instruments["fuel_consumption"].set(truck.get("fuelAverageConsumption", 0), attributes)
    instruments["oil_temp"].set(truck.get("oilTemperature", 0), attributes)
    instruments["oil_pressure"].set(truck.get("oilPressure", 0), attributes)
    instruments["water_temp"].set(truck.get("waterTemperature", 0), attributes)
    instruments["battery_voltage"].set(truck.get("batteryVoltage", 0), attributes)
    instruments["adblue"].set(truck.get("adblue", 0), attributes)

    # Brakes
    instruments["brake_temp"].set(truck.get("brakeTemperature", 0), attributes)
    instruments["air_pressure"].set(truck.get("airPressure", 0), attributes)

    # Wear
    instruments["wear_engine"].set(truck.get("wearEngine", 0), attributes)
    instruments["wear_transmission"].set(truck.get("wearTransmission", 0), attributes)
    instruments["wear_cabin"].set(truck.get("wearCabin", 0), attributes)
    instruments["wear_chassis"].set(truck.get("wearChassis", 0), attributes)
    instruments["wear_wheels"].set(truck.get("wearWheels", 0), attributes)
    instruments["trailer_wear"].set(trailer.get("wear", 0), attributes)

    # Navigation
    instruments["nav_distance"].set(nav.get("estimatedDistance", 0), attributes)
    instruments["nav_speed_limit"].set(nav.get("speedLimit", 0), attributes)

    # Odometer
    instruments["odometer"].set(truck.get("odometer", 0), attributes)

    # Logistics & Financials
    job = data.get("job", {})
    instruments["job_income"].set(job.get("income", 0), attributes)
    instruments["cargo_mass"].set(job.get("cargoMass", 0), attributes)
    
    remaining_time_str = job.get("remainingTime", "")
    instruments["job_remaining_time"].set(parse_ets2_time(remaining_time_str), attributes)

    # Driver Behavior
    instruments["cruise_control"].set(1 if truck.get("cruiseControlOn", False) else 0, attributes)
    instruments["retarder"].set(truck.get("retarderBrake", 0), attributes)

    # Advanced Physics
    truck_heading = truck.get("placement", {}).get("heading", 0)
    trailer_heading = trailer.get("placement", {}).get("heading", 0)
    if trailer.get("attached", False):
        diff = abs(truck_heading - trailer_heading)
        instruments["sway_angle"].set(min(diff, 1 - diff), attributes)
    else:
        instruments["sway_angle"].set(0, attributes)
        
    accel = truck.get("acceleration", {})
    instruments["g_force_x"].set(accel.get("x", 0), attributes)
    instruments["g_force_z"].set(accel.get("z", 0), attributes)

