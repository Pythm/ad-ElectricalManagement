# registry.py
from __future__ import annotations

from typing import Dict, Optional

class Registry:
    _cars: Dict[str, "Car"] = {}
    _chargers: Dict[str, "Charger"] = {}


    @classmethod
    def register_car(cls, car: "Car") -> None:
        """Store a Car instance in the global registry."""
        cls._cars[car.vehicle_id] = car

    @classmethod
    def register_charger(cls, charger: "Charger") -> None:
        """Store a Charger instance in the global registry."""
        cls._chargers[charger.charger_id] = charger

    @classmethod
    def get_car(cls, vehicle_id: str) -> Optional["Car"]:
        """Return the Car instance for the given ID, or ``None``."""
        return cls._cars.get(vehicle_id)

    @classmethod
    def get_charger(cls, charger_id: str) -> Optional["Charger"]:
        """Return the Charger instance for the given ID, or ``None``."""
        return cls._chargers.get(charger_id)

    @classmethod
    def set_onboard_link(cls, car: "Car", charger: "Charger") -> None:
        """
        Link a car to a onboard charger
        """
        charger.connected_vehicle = car
        car.onboard_charger = charger

    @classmethod
    def set_link(cls, car: "Car", charger: "Charger") -> None:
        """
        Link a car and a charger both in memory and in the persistent
        data structures.

        * `car.connected_charger`  ←  charger
        * `charger.connected_vehicle`  ←  car
        * `car.car_data.connected_charger_id`  ←  charger.charger_id
        """
        # In‑memory links
        car.connected_charger = charger
        charger.connected_vehicle = car

        # Persist the IDs for next restart
        car.car_data.connected_charger_id = charger.charger_id

    @classmethod
    def unlink(cls, car: "Car") -> Optional["Charger"]:
        """
        Remove the association between a car and its charger.

        Returns the charger that was detached, or ``None`` if the car
        was not linked.
        """
        charger = getattr(car, "connected_charger", None)
        if charger is None:
            return None

        # Clear persistent identifiers
        car.car_data.connected_charger_id = None

        # Clear in‑memory references
        car.connected_charger = None
        charger.connected_vehicle = None

        return charger

    @classmethod
    def unlink_by_charger(cls, charger: "Charger") -> Optional["Car"]:
        """
        Symmetric to :meth:`unlink`.  Removes the link that the charger
        has to its car, if any.

        Returns the car that was unlinked, or ``None`` if the charger had
        no car attached.
        """
        car = getattr(charger, "connected_vehicle", None)
        if car is None:
            return None
        return cls.unlink(car)