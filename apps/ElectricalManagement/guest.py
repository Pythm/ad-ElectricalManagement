""" Guest car sessions on a charger with a guest switch (the Easee).

    A guest is a car the app knows nothing about: no sensors, no integration. The owner flips an
    input_boolean when a guest plugs in; the app then creates a "dumb" Car object, links it to the
    charger and schedules it like any other car, using the kWh the owner answers in the
    notification (or the last answer / 5 kWh). When the guest unplugs the car object is removed
    again and the switch is turned off.

    One GuestManager per charger that has a `guest` switch configured. All guest state and the
    whole create / tear-down sequence live here; the charger only forwards events to it.
"""
from __future__ import annotations

from typing import Optional

from electrical_cars import Car
from pydantic_models import CarData
from registry import Registry
from utils import cancel_timer_handler

DEFAULT_GUEST_KWH = 5.0
# Minutes the Easee must stay 'disconnected' before the guest session is ended (config
# `guest_remove_after_minutes` on the easee entry). 12 minutes = the 720 s timer the app always had.
DEFAULT_GUEST_REMOVE_AFTER_MINUTES = 12


class GuestCar(Car):
    """ A Car without any sensors. `is_guest` lets the chargers keep learned values
        (max_kWh_charged, battery size) untouched by guest sessions. """

    is_guest: bool = True


class GuestManager:
    """ Owns the guest car of one charger.

        api: the ElectricalUsage app (for add_car / remove_car and the shared lists).
        charger: the Charger instance this manager belongs to (its `charger_data.guest` is the
                 input_boolean entity id). ADapi is reached through the charger.

        Lifecycle:
        * switch `on`            -> begin_guest(): create `guest_<charger_id>`, register, link, notify.
        * switch `on` at startup -> recreate_at_startup(): same, restoring kWh from a surviving queue
                                    entry; the owner wants a running guest session to survive a reload.
        * notification replies   -> set_kWh_from_reply() / charge_now(): act on the guest only.
        * charger disconnected for `guest_remove_after_minutes` (Easee timer), switch `off` by the
          owner, or a new car linked -> end_guest(): one idempotent tear-down.
    """

    def __init__(self, api, charger):
        self.manager = api
        self.charger = charger
        self.ADapi = charger.ADapi
        self.namespace = charger.namespace
        self.switch: str = charger.charger_data.guest
        self.charging_scheduler = charger.charging_scheduler

        self.guest: Optional[GuestCar] = None
        # kWh the guest asked for last time, used as start value for the next guest. Not stored between restarts.
        self.last_kWh: float = DEFAULT_GUEST_KWH

        self.switch_is_on: bool = self.ADapi.get_state(self.switch, namespace = self.namespace) == 'on'
        self.ADapi.listen_state(self.switch_changed, self.switch, namespace = self.namespace)

    # ---- identity ------------------------------------------------------------------------ #

    @property
    def guest_id(self) -> str:
        """ Stable id: one guest per charger, so orphans can not accumulate across restarts. """
        return f"guest_{self.charger.charger_id}"

    def is_guest(self, car) -> bool:
        return car is not None and car is self.guest

    # ---- switch -------------------------------------------------------------------------- #

    def switch_changed(self, entity, attribute, old, new, kwargs) -> None:
        """ listen_state callback for the guest input_boolean. """

        self.switch_is_on = new == 'on'
        self.charger.guestCharging = self.switch_is_on

        if new == 'on' and old == 'off':
            self.begin_guest(reason = 'guest switch turned on')

        elif new == 'off' and old == 'on':
            vehicle = self.charger.connected_vehicle
            if (
                vehicle is not None
                and not self.is_guest(vehicle)
                and vehicle.isConnected()
                and self.charger.kWhRemaining() > 0
            ):
                # A real car is on the charger: its kWh estimate no longer uses the guest path.
                vehicle.findNewChargeTime()
            self.end_guest(reason = 'guest switch turned off')

    # ---- creation ------------------------------------------------------------------------ #

    def begin_guest(self, reason: str, notify: bool = True) -> Optional[GuestCar]:
        """ Creates the guest car, registers it, links it to the charger and (by default) asks the
            owner for kWh / charge now. Idempotent: a second call while a guest exists does nothing. """

        if self.guest is not None:
            self.ADapi.log(f"{self.charger.charger}: guest {self.guest.carName} already exists ({reason}). Nothing to do. ###", level = 'DEBUG')
            return self.guest

        stale = Registry.get_car(self.guest_id)
        if stale is not None:
            # Can only happen if a previous manager lost track of its car. Clean it out first so
            # the Registry and self.cars never hold two objects for the same id.
            self.ADapi.log(f"{self.charger.charger}: stale guest car {self.guest_id} found in the registry. Removing it before creating a new one.", level = 'WARNING')
            self.manager.remove_car(self.guest_id)

        linked = self.charger.connected_vehicle
        if linked is not None:
            self._warn_car_linked(linked)

        guest = GuestCar(
            api = self.ADapi,
            namespace = self.namespace,
            carName = self.guest_id,
            vehicle_id = self.guest_id,
            car_data = CarData(),
            charging_scheduler = self.charging_scheduler,
        )
        guest.car_data.kWh_remain_to_charge = self.last_kWh
        self.guest = guest
        self.charger.guestCharging = True

        self.manager.add_car(guest)
        # 1:1 link: a car that was linked to this charger is detached cleanly by set_link.
        Registry.set_link(guest, self.charger)
        self.ADapi.log(f"{self.charger.charger}: guest {guest.carName} created and linked ({reason}). ###", level = 'DEBUG')

        if notify:
            self.charger.notify_charge_now_or_kWhRemain(guest.carName)
        return guest

    def recreate_at_startup(self) -> None:
        """ Called by the core once all cars and chargers are set up and linked. If the switch is
            on, the guest session is recreated so a reload does not end it. The kWh is taken from
            the guest's queue entry when one survived in the persisted charging queue; then the
            owner is not asked again. """

        if not self.switch_is_on or self.guest is not None:
            return

        restored_kWh = None
        for item in self.charging_scheduler.chargingQueue:
            if item.vehicle_id == self.guest_id and item.kWhRemaining is not None and item.kWhRemaining > 0:
                restored_kWh = float(item.kWhRemaining)
                break
        if restored_kWh is not None:
            self.last_kWh = restored_kWh

        self.begin_guest(reason = 'guest switch on at startup', notify = restored_kWh is None)
        self.ADapi.log(
            f"{self.charger.charger}: guest switch is on at startup, guest {self.guest_id} recreated"
            + (f" with {restored_kWh} kWh from the charge queue" if restored_kWh is not None else f" with {self.last_kWh} kWh")
            + ". ###",
            level = 'INFO'
        )

    def _warn_car_linked(self, car) -> None:
        message = (
            f"Guest switch on while {car.carName} is linked to {self.charger.charger}. "
            f"The guest takes the charger and {car.carName} is detached."
        )
        self.ADapi.log(message, level = 'WARNING')
        try:
            self.charger.notify_app.send_notification(
                message = message,
                message_title = f"{self.charger.charger}",
                message_recipient = self.charger.recipients,
                also_if_not_home = True,
                data = {'tag': 'guest' + str(self.charger.charger)}
            )
        except Exception as e:
            self.ADapi.log(f"{self.charger.charger} could not send notification: {e}", level = 'DEBUG')

    # ---- notification replies ------------------------------------------------------------ #

    def set_kWh(self, kWh: float) -> None:
        """ Sets kWh to charge for the guest car and remembers it for the next guest.
            Only the guest car is changed, never a Tesla that happens to be on the charger. """

        if kWh <= 0:
            raise ValueError(f"kWh must be above 0, got {kWh}")
        self.last_kWh = kWh
        if self.guest is None:
            self.ADapi.log(f"{self.charger.charger}: kWh {kWh} received for a guest car but no guest car exists. Ignored.", level = 'INFO')
            return
        self.guest.car_data.kWh_remain_to_charge = kWh

    def set_kWh_from_reply(self, reply_text) -> bool:
        """ 'kWhremaining<charger>' notification action. Returns False (with an INFO log) when
            there is no guest. A reply that is not a number keeps the current estimate. """

        if self.guest is None:
            self.ADapi.log(f"kWh remaining received for {self.charger.charger} but there is no guest car. Ignored.", level = 'INFO')
            return False
        try:
            self.set_kWh(float(str(reply_text).replace(',', '.')))
        except (ValueError, TypeError):
            self.charger.kWhRemaining()
            self.ADapi.log(
                f"User input {reply_text} on setting kWh remaining for Guest car. Not valid number. "
                f"Using {self.guest.car_data.kWh_remain_to_charge} to calculate charge time",
                level = 'INFO'
            )
        if self.guest is not None:
            self.guest.findNewChargeTime()
        return True

    def charge_now(self) -> bool:
        """ 'chargeNow<charger>' notification action: start the guest right away. """

        if self.guest is None:
            self.ADapi.log(f"Charge now received for {self.charger.charger} but there is no guest car. Ignored.", level = 'INFO')
            return False
        self.guest.charge_now = True
        if self.charger.connected_vehicle is self.guest:
            self.charger.startCharging()
        else:
            self.ADapi.log(f"Charge now for guest on {self.charger.charger}: guest is not linked to the charger, not starting.", level = 'INFO')
        return True

    # ---- tear-down ----------------------------------------------------------------------- #

    def end_guest(self, reason: str) -> bool:
        """ Ends the guest session. Idempotent: returns False when there is no guest.

            Order: cancel the guest's charger timers, remove the guest from the charge queue and
            the queue/solar lists and from currentlyCharging, unregister it (Registry + cars),
            pause the charger ONLY if the guest is still the charger's vehicle, unlink, and turn
            the switch off. The switch-off listener then finds no guest and does nothing. """

        guest = self.guest
        if guest is None:
            return False
        self.guest = None
        charger = self.charger
        vehicle_id = guest.vehicle_id
        guest_is_vehicle = charger.connected_vehicle is guest
        self.ADapi.log(f"{charger.charger}: guest session {vehicle_id} ends: {reason}.", level = 'INFO')

        # 1. Timers: the start/stop verify loop of the charger belongs to the guest when the guest
        #    (or nobody) is the charger's vehicle. Includes the resume loop that charge_now keeps alive.
        if guest_is_vehicle or charger.connected_vehicle is None:
            if cancel_timer_handler(ADapi = self.ADapi, handler = charger.checkCharging_handler, name = charger.charger):
                charger.checkCharging_handler = None

        # 2. Queues and lists, 3. Registry and cars (ElectricalUsage.remove_car does both).
        self.manager.remove_car(vehicle_id)

        # 4. Pause the charger only when the guest's session is the current one. force_stop: the
        #    guest is gone, charge_now must not keep the charger running without a car object.
        if guest_is_vehicle:
            charger.stopCharging(force_stop = True)
            # The pause is sent once; the removed car must not be verified against.
            if cancel_timer_handler(ADapi = self.ADapi, handler = charger.checkCharging_handler, name = charger.charger):
                charger.checkCharging_handler = None
            self.ADapi.log(f"{charger.charger} paused for guest {vehicle_id} ###", level = 'DEBUG')

        # 5. Unlink (only clears the charger side if it still points at the guest).
        Registry.unlink(guest)

        # 6. Switch off. Not resent when HA already reports off (owner switched it off).
        self.charger.guestCharging = False
        self.switch_is_on = False
        if self.ADapi.get_state(self.switch, namespace = self.namespace) != 'off':
            self.ADapi.call_service('input_boolean/turn_off',
                entity_id = self.switch,
                namespace = self.namespace,
            )
        return True
