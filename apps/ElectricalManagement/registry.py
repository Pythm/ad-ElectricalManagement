# registry.py
from __future__ import annotations

from typing import Dict, Optional

class Registry:
    """ Process-wide lookup of Car and Charger instances by id.

        The dicts are class attributes, so they survive an AppDaemon app reload. ElectricalUsage
        therefore calls :meth:`clear` at the top of ``initialize`` so stale objects from the previous
        run are never returned.
    """
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
    def unregister_car(cls, vehicle_id: str) -> Optional["Car"]:
        """Remove and return the Car with the given id, or ``None`` if not registered."""
        return cls._cars.pop(vehicle_id, None)

    @classmethod
    def unregister_charger(cls, charger_id: str) -> Optional["Charger"]:
        """Remove and return the Charger with the given id, or ``None`` if not registered."""
        return cls._chargers.pop(charger_id, None)

    @classmethod
    def clear(cls) -> None:
        """Forget every registered car and charger (used on app (re)initialisation)."""
        cls._cars.clear()
        cls._chargers.clear()

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
        data structures. Links are kept 1:1:

        * the charger's previous car (if another car was linked to this charger) is detached:
          its ``connected_charger`` and persisted ``connected_charger_id`` are cleared.
        * the car's previous charger (if the car was linked to another charger) is detached:
          its ``connected_vehicle`` is cleared - EXCEPT when that charger is the car's own
          onboard charger. The onboard charger keeps pointing at its car (``set_onboard_link``
          semantics) while the car is linked to an external charger such as the Easee; the
          Tesla_charger auto-link on 'Stopped' and the onboard start/stop commands depend on it.

        * `car.connected_charger`  ←  charger
        * `charger.connected_vehicle`  ←  car
        * `car.car_data.connected_charger_id`  ←  charger.charger_id
        """
        if car is None or charger is None:
            return

        # Detach the charger's previous car
        previous_car = getattr(charger, "connected_vehicle", None)
        if previous_car is not None and previous_car is not car:
            if getattr(previous_car, "connected_charger", None) is charger:
                previous_car.connected_charger = None
                previous_car.car_data.connected_charger_id = None

        # Detach the car's previous charger (never the car's own onboard charger)
        previous_charger = getattr(car, "connected_charger", None)
        if (
            previous_charger is not None
            and previous_charger is not charger
            and previous_charger is not getattr(car, "onboard_charger", None)
            and getattr(previous_charger, "connected_vehicle", None) is car
        ):
            previous_charger.connected_vehicle = None

        # In‑memory links
        car.connected_charger = charger
        charger.connected_vehicle = car

        # Persist the IDs for next restart
        car.car_data.connected_charger_id = charger.charger_id

    @classmethod
    def unlink(cls, car: "Car") -> Optional["Charger"]:
        """
        Remove the association between a car and its charger.

        The charger side is only cleared when the charger still points at this car,
        so unlinking a stale car never drops another car that was linked meanwhile.

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
        if getattr(charger, "connected_vehicle", None) is car:
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

    @classmethod
    def relink_to_onboard(cls, charger: "Charger") -> Optional["Car"]:
        """
        Symmetric to :meth:`unlink`.  Removes the link that the charger
        has to its car, if any.

        Returns the car that was unlinked, or ``None`` if the charger had
        no car attached.
        """
        car = getattr(charger, "connected_vehicle", None)
        if car is None:
            return None
        charger_to_return = cls.unlink(car)
        onboard = getattr(car, "onboard_charger", None)
        if onboard is not None:
            cls.set_link(car, onboard)
        return charger_to_return
