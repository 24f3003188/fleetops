"""
FleetOps — ETS2 Observability Engine
Events module: detects state changes and emits structured OTel log records.
"""

import logging
from opentelemetry._logs import SeverityNumber


class EventDetector:
    """
    Compares consecutive telemetry snapshots to detect meaningful events
    and emits them as structured log records via Python's logging module
    (which is wired to OTel LoggerProvider in main.py).
    """

    def __init__(self):
        self.logger = logging.getLogger("fleetops.events")
        self._prev = {}

    def _changed(self, key: str, current: bool) -> bool:
        """Return True if a boolean field just transitioned to True."""
        prev = self._prev.get(key, False)
        return current and not prev

    def _decreased(self, key: str, current: float, threshold: float) -> bool:
        """Return True if a value just crossed below a threshold."""
        prev = self._prev.get(key, current)
        return prev >= threshold and current < threshold

    def check(self, data: dict, attributes: dict) -> list[dict]:
        """Run all event detectors against the current telemetry snapshot.
        Emits log records and returns a list of detected event dictionaries."""
        detected_events = []


        truck = data.get("truck", {})
        trailer = data.get("trailer", {})
        nav = data.get("navigation", {})
        game = data.get("game", {})

        # --- Engine Events ---
        engine_on = truck.get("engineOn", False)
        if self._changed("engine_on", engine_on):
            evt = {"event.name": "fleet.engine.start", **attributes}
            self.logger.info("Engine started", extra=evt)
            detected_events.append(evt)
        if not engine_on and self._prev.get("engine_on", False):
            evt = {"event.name": "fleet.engine.stop", **attributes}
            self.logger.info("Engine stopped", extra=evt)
            detected_events.append(evt)

        # --- Fuel Warning ---
        fuel_warning = truck.get("fuelWarningOn", False)
        if self._changed("fuel_warning", fuel_warning):
            evt = {
                "event.name": "fleet.fuel.warning",
                "ets2.fuel_level": truck.get("fuel", 0),
                **attributes
            }
            self.logger.warning("Low fuel warning activated! Fuel: %.1f L", truck.get("fuel", 0), extra=evt)
            detected_events.append(evt)

        # --- Air Pressure Emergency ---
        air_emergency = truck.get("airPressureEmergencyOn", False)
        if self._changed("air_emergency", air_emergency):
            evt = {
                "event.name": "fleet.air_pressure.emergency",
                "ets2.air_pressure": truck.get("airPressure", 0),
                **attributes
            }
            self.logger.error("AIR PRESSURE EMERGENCY! Emergency brakes activated. Pressure: %.1f psi", truck.get("airPressure", 0), extra=evt)
            detected_events.append(evt)

        # --- Water Temperature Warning ---
        water_warn = truck.get("waterTemperatureWarningOn", False)
        if self._changed("water_warn", water_warn):
            evt = {
                "event.name": "fleet.water_temp.warning",
                "ets2.water_temp": truck.get("waterTemperature", 0),
                **attributes
            }
            self.logger.warning("Engine overheating! Water temp: %.1f°C", truck.get("waterTemperature", 0), extra=evt)
            detected_events.append(evt)

        # --- Oil Pressure Warning ---
        oil_warn = truck.get("oilPressureWarningOn", False)
        if self._changed("oil_warn", oil_warn):
            evt = {
                "event.name": "fleet.oil_pressure.warning",
                "ets2.oil_pressure": truck.get("oilPressure", 0),
                **attributes
            }
            self.logger.warning("Low oil pressure warning! Pressure: %.1f psi", truck.get("oilPressure", 0), extra=evt)
            detected_events.append(evt)

        # --- Battery Voltage Warning ---
        battery_warn = truck.get("batteryVoltageWarningOn", False)
        if self._changed("battery_warn", battery_warn):
            evt = {
                "event.name": "fleet.battery.warning",
                "ets2.battery_voltage": truck.get("batteryVoltage", 0),
                **attributes
            }
            self.logger.warning("Battery voltage warning! Voltage: %.1f V", truck.get("batteryVoltage", 0), extra=evt)
            detected_events.append(evt)

        # --- Speeding Detection ---
        speed = abs(truck.get("speed", 0))
        speed_limit = nav.get("speedLimit", 0)
        was_speeding = self._prev.get("is_speeding", False)
        is_speeding = speed_limit > 0 and speed > speed_limit + 5  # 5 km/h grace
        if is_speeding:
            evt = {
                "event.name": "fleet.speeding",
                "ets2.speed": speed,
                "ets2.speed_limit": speed_limit,
                **attributes
            }
            self.logger.warning("SPEEDING! %.0f km/h in a %d km/h zone", speed, speed_limit, extra=evt)
            detected_events.append(evt)

        # --- Damage Detection (wear spike) ---
        wear_keys = [
            ("wearEngine", "wear_engine"),
            ("wearTransmission", "wear_transmission"),
            ("wearCabin", "wear_cabin"),
            ("wearChassis", "wear_chassis"),
            ("wearWheels", "wear_wheels"),
        ]
        for api_key, state_key in wear_keys:
            current_wear = truck.get(api_key, 0)
            prev_wear = self._prev.get(state_key, current_wear)
            delta = current_wear - prev_wear
            if delta > 0.02:  # 2% sudden increase = collision
                component = api_key.replace("wear", "").lower() or "unknown"
                evt = {
                    "event.name": "fleet.damage.increased",
                    "ets2.component": component,
                    "ets2.wear_delta": delta,
                    "ets2.wear_current": current_wear,
                    **attributes
                }
                self.logger.warning("Damage detected on %s! Wear jumped by %.1f%%", component, delta * 100, extra=evt)
                detected_events.append(evt)

        # --- Park Brake ---
        park_brake = truck.get("parkBrakeOn", False)
        if self._changed("park_brake", park_brake):
            evt = {"event.name": "fleet.park_brake.on", **attributes}
            self.logger.info("Parking brake engaged", extra=evt)
            detected_events.append(evt)

        # --- Harsh Braking Detection (Legacy Speed Delta) ---
        speed = abs(truck.get("speed", 0))
        speed_history = self._prev.get("speed_history", [])
        speed_history.append(speed)
        if len(speed_history) > 4:
            speed_history.pop(0)

        if len(speed_history) == 4:
            old_speed = speed_history[0]
            # 15 km/h drop over 4 ticks (approx 2 seconds)
            if old_speed - speed > 15:
                if not self._prev.get("harsh_braking_active"):
                    evt = {
                        "event.name": "fleet.harsh_braking",
                        "ets2.speed_drop": old_speed - speed,
                        **attributes
                    }
                    self.logger.warning("HARSH BRAKING DETECTED! Dropped %.0f km/h in 2s", old_speed - speed, extra=evt)
                    detected_events.append(evt)
                    self._prev["harsh_braking_active"] = True
            else:
                self._prev["harsh_braking_active"] = False

        # --- G-Force & Cornering Risk ---
        accel_x = truck.get("acceleration", {}).get("x", 0)
        accel_z = truck.get("acceleration", {}).get("z", 0)
        
        if abs(accel_z) > 4.5:
             if not self._prev.get("gforce_braking_active", False):
                  evt = {"event.name": "fleet.safety.harsh_braking_gforce", "ets2.g_force_z": accel_z, **attributes}
                  self.logger.warning("HARSH BRAKING (G-FORCE)! %.2f m/s^2", accel_z, extra=evt)
                  detected_events.append(evt)
                  self._prev["gforce_braking_active"] = True
        else:
             self._prev["gforce_braking_active"] = False
             
        if abs(accel_x) > 3.0 and speed > 40:
             if not self._prev.get("gforce_cornering_active", False):
                  evt = {"event.name": "fleet.safety.dangerous_cornering", "ets2.g_force_x": accel_x, **attributes}
                  self.logger.warning("DANGEROUS CORNERING! %.2f lateral m/s^2", accel_x, extra=evt)
                  detected_events.append(evt)
                  self._prev["gforce_cornering_active"] = True
        else:
             self._prev["gforce_cornering_active"] = False

        # --- Jackknife / Sway Risk ---
        trailer_attached = trailer.get("attached", False)
        truck_heading = truck.get("placement", {}).get("heading", 0)
        trailer_heading = trailer.get("placement", {}).get("heading", 0)
        if trailer_attached:
            diff = abs(truck_heading - trailer_heading)
            sway_angle = min(diff, 1 - diff)
            if sway_angle > 0.04:  # ~15 degrees in 0-1 unit range
                if not self._prev.get("jackknife_active", False):
                    evt = {"event.name": "fleet.safety.jackknife_risk", "ets2.sway_angle": sway_angle, **attributes}
                    self.logger.error("JACKKNIFE RISK! High trailer sway detected.", extra=evt)
                    detected_events.append(evt)
                    self._prev["jackknife_active"] = True
            else:
                self._prev["jackknife_active"] = False

        # --- Ferry / Train Transport Detection ---
        placement = truck.get("placement", {})
        curr_x = placement.get("x", 0)
        curr_z = placement.get("z", 0)
        prev_x = self._prev.get("pos_x", curr_x)
        prev_z = self._prev.get("pos_z", curr_z)
        
        # Calculate Euclidean displacement in meters
        import math
        disp_meters = math.hypot(curr_x - prev_x, curr_z - prev_z)
        if disp_meters > 50000 and prev_x != 0:  # > 50 km jump in 1 tick = Ferry / Eurotunnel
            evt = {
                "event.name": "fleet.transport.ferry",
                "ets2.ferry_distance_km": disp_meters / 1000.0,
                **attributes
            }
            self.logger.info("FERRY / TRAIN TRANSIT DETECTED! Teleported %.0f km across sea/tunnel", disp_meters / 1000.0, extra=evt)
            detected_events.append(evt)

        # --- Grade-aware Braking Technique ---
        pitch = truck.get("placement", {}).get("pitch", 0)
        retarder = truck.get("retarderBrake", 0)
        brake_temp = truck.get("brakeTemperature", 0)
        
        if pitch < -0.05 and retarder == 0 and brake_temp > 50:
            if not self._prev.get("grade_braking_active", False):
                evt = {"event.name": "fleet.safety.poor_braking", "ets2.pitch": pitch, "ets2.brake_temp": brake_temp, **attributes}
                self.logger.warning("Poor braking technique on descent! Use retarder.", extra=evt)
                detected_events.append(evt)
                self._prev["grade_braking_active"] = True
        else:
            self._prev["grade_braking_active"] = False

        # --- Save state for next cycle ---
        self._prev = {
            "engine_on": engine_on,
            "fuel_warning": fuel_warning,
            "air_emergency": air_emergency,
            "water_warn": water_warn,
            "oil_warn": oil_warn,
            "battery_warn": battery_warn,
            "is_speeding": is_speeding,
            "park_brake": park_brake,
            "speed_history": speed_history,
            "harsh_braking_active": self._prev.get("harsh_braking_active", False),
            "gforce_braking_active": self._prev.get("gforce_braking_active", False),
            "gforce_cornering_active": self._prev.get("gforce_cornering_active", False),
            "jackknife_active": self._prev.get("jackknife_active", False),
            "grade_braking_active": self._prev.get("grade_braking_active", False),
            "pos_x": curr_x,
            "pos_z": curr_z,
        }
        for api_key, state_key in wear_keys:
            self._prev[state_key] = truck.get(api_key, 0)
            
        return detected_events
