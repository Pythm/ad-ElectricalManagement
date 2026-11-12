from __future__ import annotations

import bisect
import math
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from pydantic_models import ChargingQueueItem, WattSlot
from registry import Registry
from utils import NO_TOMORROW_PRICES_WINDOW, cancel_timer_handler, get_next_runtime_aware, price_now_or_inf, slot_index_now, to_float_or_none

LOW_PRICE_PATH_WINDOW = ("09:00:00", "14:00:00")   # without tomorrow's prices a low price is charging time here
INFO_UPDATE_DELAY_SECONDS = 3   # lets several queue changes in a row end in one update
INFOTEXT_MAX_LENGTH = 255       # the most a Home Assistant input_text can hold
INFOTEXT_NOTHING_PLANNED = "No planned charging"

class Scheduler:
    """ Class for calculating and schedule charge times.

        `chargingQueue` and `available_watt` are the very list objects stored in the main app's
        PersistenceData and are shared BY REFERENCE: the scheduler mutates them in place and
        the main app refreshes `available_watt` with .clear()/.extend() so this reference
        stays valid. Never rebind these attributes.
    """

    def __init__(self, api,
        stopAtPriceIncrease:float,
        startBeforePrice:float,
        infotext,
        namespace:str,
        electricalPriceApp,
        notify_app,
        recipients,
        chargingQueue: Optional[list[ChargingQueueItem]] = None,
        available_watt: Optional[List[WattSlot]] = None,
        registry: Optional[Registry] = None,
    ):
        self.ADapi = api
        self.namespace = namespace
        self.electricalPriceApp = electricalPriceApp
        self.notify_app = notify_app
        self.recipients = recipients
        self.stopAtPriceIncrease = stopAtPriceIncrease
        self.startBeforePrice = startBeforePrice
        self.infotext = infotext

        self.chargingQueue: list[ChargingQueueItem] = chargingQueue
        self.available_watt: List[WattSlot] = available_watt
        # The cars and chargers of this app, see Registry.
        self.registry: Registry = registry if registry is not None else Registry()

        self.currentlyCharging: set[str] = set()
        self.informHandler = None
        self._last_plan = None

        # helper values
        now = self.ADapi.datetime(aware=True)
        self.save_endHour = now.replace(minute=0, second=0, microsecond=0)

    def _calculate_expected_chargetime(
        self,
        kWhRemaining: float = 2,
        totalW_AllChargers: float = 3600,
        start_time: Optional[datetime] = None,
    ) -> float:
        """ Estimate the *number of hours* it will take to finish a charge """

        if start_time is None:
            start_time = self.ADapi.datetime(aware=True)

        if start_time > self.save_endHour:
            self.save_endHour = get_next_runtime_aware(
                startTime = start_time, offset_seconds=0, delta_in_seconds=60 * 15
            )

        wh_remaining = kWhRemaining * 1_000
        if not self.available_watt:
            # No per-slot budget yet (prices not received / idle consumption not calculated):
            # fall back to a plain power-based estimate instead of claiming 0 hours.
            return wh_remaining / max(totalW_AllChargers, 1)

        idx_start = bisect.bisect_left([s.start for s in self.available_watt], self.save_endHour)

        hours_to_charge = 0.0

        for slot in self.available_watt[idx_start:]:
            if wh_remaining <= 0:
                break

            # A slot can be below zero when idle use is above the hourly cap: it gives nothing.
            usable_wh = max(0.0, min(
                slot.available_Wh,
                totalW_AllChargers * slot.duration_hours,
            ))

            if wh_remaining <= usable_wh:
                hours_to_charge += slot.duration_hours
                wh_remaining = 0
            else:
                wh_remaining -= usable_wh
                hours_to_charge += slot.duration_hours

        if wh_remaining > 0 and self.available_watt:
            last = self.available_watt[-1]
            if last.available_Wh > 0:
                extra = (wh_remaining / last.available_Wh) * last.duration_hours
                hours_to_charge += extra

        return hours_to_charge

    def _entry_for(self, vehicle_id: str) -> Optional["ChargingQueueItem"]:
        """ Return the first queue item that belongs to *vehicle_id* or ``None`` """

        return next((c for c in self.chargingQueue if c.vehicle_id == vehicle_id), None)

    def _vehicle_priority_map(self) -> dict[str, int]:
        """ A lookup table that maps every vehicle_id that is currently in the *charging* queue to its `priority` value. """

        return {item.vehicle_id: item.priority for item in self.chargingQueue}

    def sort_charging_queue_by_priority(
        self,
        vehicle_ids: List[str],
        *,
        reverse: bool = False
    ) -> List[str]:
        """ Return a **new list** containing the same vehicle_ids but sorted
        according to the priority stored in `self.chargingQueue`.
        Callers that need the persisted queue reordered must assign back with
        ``charging_list[:] = ...`` so the list object (shared with PersistenceData) is kept. """

        priority_map = self._vehicle_priority_map()

        def priority_of(vid: str) -> int:
            return priority_map.get(vid, 5)

        return sorted(vehicle_ids, key=priority_of, reverse=reverse)

    def getChargingTime(self, vehicle_id: str) -> Tuple[Optional[datetime], Optional[datetime]]:
        """ Return ``(charging_start, charging_stop)`` for *vehicle_id* if the
        queue item has both timestamps set, otherwise ``(None, None)`` """

        entry = self._entry_for(vehicle_id)
        if entry and entry.chargingStart and entry.chargingStop:
            return entry.chargingStart, entry.chargingStop
        return None, None

    def isChargingTime(self, vehicle_id: Optional[str] = None) -> bool:
        """ Return ``True`` if *now* lies between a chargingStart/Stop pair for the
        supplied vehicle (or for any vehicle when *vehicle_id* is ``None``) """

        if not self.chargingQueue:
            return False

        now = self.ADapi.datetime(aware=True)

        for entry in self.chargingQueue:
            if vehicle_id is not None and entry.vehicle_id != vehicle_id:
                continue

            if entry.chargingStart and entry.chargingStop and entry.chargingStart <= now < entry.chargingStop:
                return True

            if entry.charge_below != 0:
                # No current price (inf) -> not cheap -> do not charge on price.
                cheap = price_now_or_inf(self.electricalPriceApp) <= entry.charge_below
                if cheap or vehicle_id is not None:
                    return cheap

        if (
            self.ADapi.now_is_between(*LOW_PRICE_PATH_WINDOW)
            and not self.electricalPriceApp.tomorrow_valid
        ):
            return self._is_charging_price(vehicle_id = vehicle_id)

        return False

    def _is_charging_price(self, vehicle_id: Optional[str] = None) -> bool:
        max_price = 0.0
        for entry in self.chargingQueue:
            if entry.price is not None and entry.price > max_price:
                max_price = entry.price

        if max_price == 0:
            max_price = self._update_prices_for_future_hours()
            if max_price is None or max_price == -1:
                return False
        return price_now_or_inf(self.electricalPriceApp) <= max_price


    def _update_prices_for_future_hours(self) -> float:
        """ When tomorrow's price data is not yet available.
            Returns the price limit, or -1 when no limit could be found (treated as 'not charging time'). """

        now = self.ADapi.datetime(aware=True)
        if all(c.price is not None for c in self.chargingQueue):
            return -1

        kWh_to_charge = sum(c.kWhRemaining for c in self.chargingQueue if c.kWhRemaining is not None)
        total_power = sum(c.maxAmps * c.voltPhase for c in self.chargingQueue if c.maxAmps and c.voltPhase)

        if not any(c.estHourCharge for c in self.chargingQueue):
            est_hours = self._calculate_expected_chargetime(
                kWhRemaining = kWh_to_charge,
                totalW_AllChargers = total_power
            )
        else:
            est_hours = sum(c.estHourCharge for c in self.chargingQueue if c.estHourCharge)

        # checkitem is a SLOT INDEX into elpricestoday (24/day hourly, 96/day in 15-minute mode),
        # not an hour. On a normal hourly day starting at 00:00 the index of 'now' equals now.hour.
        price = self.electricalPriceApp.get_lowest_prices(
            checkitem = slot_index_now(self.electricalPriceApp.elpricestoday, now),
            hours = est_hours,
            min_change = 0.1
        )
        if price is None:
            # Price app has no data yet: no limit available, retry on the next call.
            return -1

        for c in self.chargingQueue:
            c.price = price
        return price

    def isWaitingForPrices(self, vehicle_id: str) -> bool:
        """ True when the car is queued with something left to charge and the price app gave it no
            window. A car that is charging is not stopped for that. A car on a fixed price has no
            window either, but its price decides. """

        entry = self._entry_for(vehicle_id)
        return entry is not None and entry.charge_below == 0 and not self._window_not_ended(entry, self.ADapi.datetime(aware=True))

    def isPastChargingTime(self, vehicle_id: Optional[str] = None) -> bool:
        """ Return ``True`` when the charging stop time for *vehicle_id* has
        already passed """

        now = self.ADapi.datetime(aware=True)
        entry = self._entry_for(vehicle_id) if vehicle_id else None
        if not entry or entry.chargingStop is None:
            return False
        return now > entry.chargingStop

    def charging_scheduled_with_updated_data(
        self,
        vehicle_id: str,
        kWhRemaining: float,
        finish_by_hour: int,
    ) -> bool:
        """ Return ``True`` if a matching queue entry exists **and** the
        scheduled charging has not yet finished """

        now = self.ADapi.datetime(aware=True)
        entry = self._entry_for(vehicle_id)
        if not entry:
            return False

        if (
            entry.kWhRemaining == kWhRemaining
            and entry.finish_by_hour == finish_by_hour
            and entry.chargingStart
            and entry.chargingStop
        ):
            return now < entry.chargingStop

        return False

    def markAsCharging(self, vehicle_id):
        if vehicle_id in self.currentlyCharging:
            return
        self.currentlyCharging.add(vehicle_id)

    def removeFromCharging(self, vehicle_id):
        if vehicle_id in self.currentlyCharging:
            self.currentlyCharging.discard(vehicle_id)

    def isCurrentlyCharging(self, vehicle_id):
        return vehicle_id in self.currentlyCharging

    def findNextChargerToStart(self, check_if_charging_time:bool = True) -> Optional[str]:
        """ Return the *vehicle_id* of the next charging job that is ready to start """

        for priority in range(1, 6):
            for entry in self.chargingQueue:
                if entry.priority == priority or priority == 5:
                    if (
                        not self.isCurrentlyCharging(entry.vehicle_id) and
                        (self.isChargingTime(entry.vehicle_id) or not check_if_charging_time)
                    ):
                        return entry.vehicle_id
        return None

    def _remove_entry(self, vehicle_id: str) -> bool:
        """ Remove the first queue entry that matches *vehicle_id* """

        for idx, entry in enumerate(self.chargingQueue):
            if entry.vehicle_id == vehicle_id:
                del self.chargingQueue[idx]
                return True
        return False

    def removeFromQueue(self, vehicle_id: str) -> None:
        """ Remove *vehicle_id* from the queue and update the info text """

        if self._remove_entry(vehicle_id):
            self.schedule_info_update()

    def _window_not_ended(self, entry: ChargingQueueItem, now: datetime) -> bool:
        """ True when the entry has a charging window that is running or still ahead. """

        return bool(entry.chargingStart and entry.chargingStop and now < entry.chargingStop)

    def queueForCharging(
        self,
        vehicle_id: str,
        kWhRemaining: float,
        maxAmps: int,
        voltPhase: int,
        finish_by_hour: int,
        priority: int,
        name: str,
        charge_below: float,
    ) -> bool:
        """ Enqueue a new charging job (or replace an existing one) """

        previous = self._entry_for(vehicle_id)
        self._remove_entry(vehicle_id)

        if kWhRemaining <= 0:
            return False

        est_hour_charge = self._calculate_expected_chargetime(
            kWhRemaining=kWhRemaining,
            totalW_AllChargers=maxAmps * voltPhase,
        )

        new_item = ChargingQueueItem(
            vehicle_id=vehicle_id,
            kWhRemaining=kWhRemaining,
            maxAmps=maxAmps,
            voltPhase=voltPhase,
            finish_by_hour=finish_by_hour,
            priority=priority,
            estHourCharge=est_hour_charge,
            name=name,
            charge_below=charge_below,
        )
        if previous is not None:
            # What the owner was told survives the re-plan: an unchanged plan is not sent again.
            new_item.informedStart = previous.informedStart
            new_item.informedStop = previous.informedStop
            new_item.informedBelow = previous.informedBelow
            if charge_below == 0 and self._window_not_ended(previous, self.ADapi.datetime(aware=True)):
                # process_charging_queue replaces this window when the price app finds a new one.
                new_item.chargingStart = previous.chargingStart
                new_item.chargingStop = previous.chargingStop
                new_item.estimateStop = previous.estimateStop
                new_item.price = previous.price
        self.chargingQueue.append(new_item)

        if self.ADapi.now_is_between(*NO_TOMORROW_PRICES_WINDOW) and not self.electricalPriceApp.tomorrow_valid:
            # The price app has no window to give yet. A car with a window keeps it. The others get one when
            # tomorrow's prices arrive (ElectricalUsage._get_new_prices), and can charge on a low price until then.
            self.ADapi.log(f"{name}: no tomorrow prices yet, keeping window {new_item.chargingStart} - {new_item.chargingStop}", level = 'DEBUG') ###
            return self.isChargingTime(vehicle_id=vehicle_id)

        self.process_charging_queue()
        return self.isChargingTime(vehicle_id=vehicle_id)

    def process_charging_queue(self) -> None:
        """ Resolve the whole queue, scheduling charging windows, detecting
        simultaneous sessions and finally computing the “best” price block
        for each job.

        A window that has not ended is kept when the price app has no new one. Without tomorrow's
        prices it can not have one (NO_TOMORROW_PRICES_WINDOW), and an entry only has a window then
        if it was planned earlier, so the car has been plugged in since before that window began. """

        self.chargingQueue.sort(key=lambda c: c.finish_by_hour)
        now = self.ADapi.datetime(aware=True)

        overlapping: List[ChargingQueueItem] = []

        for current_car in self.chargingQueue:
            if current_car.charge_below != 0:
                current_car.price = current_car.charge_below
                continue

            start, stop, price = self.electricalPriceApp.get_Continuous_Cheapest_Time(
                hoursTotal=current_car.estHourCharge,
                calculateBeforeNextDayPrices=False,
                finishByHour=current_car.finish_by_hour,
                startBeforePrice=self.startBeforePrice,
                stopAtPriceIncrease=self.stopAtPriceIncrease,
            )
            if start is None and self._window_not_ended(current_car, now):
                self.ADapi.log(f"{current_car.name}: price app has no window, keeping {current_car.chargingStart} - {current_car.chargingStop}", level = 'DEBUG') ###
            else:
                current_car.chargingStart, current_car.chargingStop, current_car.price = start, stop, price
                if start is not None:
                    estMinutesToCharge = int(math.ceil(current_car.estHourCharge * 60))
                    current_car.estimateStop = start + timedelta(minutes = estMinutesToCharge)

            if current_car.chargingStart is None or current_car.chargingStop is None:
                continue

            if overlapping and not any(
                current_car.chargingStart < other.chargingStop and other.chargingStart < current_car.chargingStop
                for other in overlapping
            ):
                self._merge_overlapping(overlapping)
                overlapping = []
            overlapping.append(current_car)

        self._merge_overlapping(overlapping)

    def _merge_overlapping(self, overlapping: List[ChargingQueueItem]) -> None:
        """ Cars whose windows overlap charge at the same time and get one window. A car alone keeps its own. """

        self.ADapi.log(f"Charge group: {[(c.name, c.chargingStart, c.chargingStop) for c in overlapping]}", level = 'DEBUG') ###
        if len(overlapping) > 1:
            self.calcSimultaneousCharge([c.vehicle_id for c in overlapping])

    def calcSimultaneousCharge(self, simultaneous_charge: List[str]) -> None:
        """ Re-calculate the charging window for a group of vehicles that must run
        at the same time.  The function updates the queue in place """

        finish_by_hour = 0
        kWh_to_charge = 0.0
        total_w_all_chargers = 0.0
        start_time = self.ADapi.datetime(aware=True)

        simultaneous_items = [c for c in self.chargingQueue if c.vehicle_id in simultaneous_charge]

        simultaneous_items.sort(key=lambda c: c.priority)

        for c in simultaneous_items:
            kWh_to_charge += c.kWhRemaining
            total_w_all_chargers += c.maxAmps * c.voltPhase

            match (finish_by_hour, c.finish_by_hour, c.estHourCharge):
                case (0, _, _):
                    finish_by_hour = c.finish_by_hour

                case (f, target, _) if target <= f:
                    pass

                # 3. If adding the charge stays WITHIN the target window
                case (f, target, charge) if f + charge < target:
                    finish_by_hour += charge

                case (f, target, charge) if f + charge >= target:
                    finish_by_hour = c.finish_by_hour

                case _:
                    pass

            if c.chargingStart is not None:
                start_time = c.chargingStart

        hours_to_charge = self._calculate_expected_chargetime(
            kWhRemaining=kWh_to_charge,
            totalW_AllChargers=total_w_all_chargers,
            start_time=start_time,
        )
        charging_at, charging_stop, price = self.electricalPriceApp.get_Continuous_Cheapest_Time(
            hoursTotal=hours_to_charge,
            calculateBeforeNextDayPrices=False,
            finishByHour=finish_by_hour,
            startBeforePrice=self.startBeforePrice,
            stopAtPriceIncrease=self.stopAtPriceIncrease,
        )

        if charging_at is None:
            # (None, None, price) is returned between 06:00 and 15:00 without tomorrow's prices,
            # and (None, None, None) when no prices exist. Nothing to schedule yet.
            self.ADapi.log(
                f"No continuous charge window found for simultaneous charge of {simultaneous_charge}. "
                f"Keeping individual charge times.",
                level = 'DEBUG'
            )
            return

        eta_stop_simultaneous = charging_at + timedelta(hours = hours_to_charge)
        if charging_stop is not None:
            for c in simultaneous_items:
                c.chargingStart = charging_at
                c.chargingStop = charging_stop
                c.price = price
                c.estimateStop = eta_stop_simultaneous


    def schedule_info_update(self) -> None:
        """ Updates the info text and notification shortly, once for several changes in a row. """

        cancel_timer_handler(ADapi = self.ADapi, handler = self.informHandler, name = "charge queue info")
        self.informHandler = self.ADapi.run_in(self.notifyChargeTime, INFO_UPDATE_DELAY_SECONDS)

    def _current_plan(self, now: datetime) -> Tuple[dict, List[str], dict]:
        """ What is planned now: {(start, stop): [car names]}, the names of the cars queued without a
            window, and {charge_below: [car names]}. """

        windows: dict[Tuple[datetime, datetime], List[str]] = {}
        waiting: List[str] = []
        below: dict[float, List[str]] = {}
        for entry in sorted(self.chargingQueue, key=lambda c: c.finish_by_hour):
            if entry.charge_below != 0:
                below.setdefault(entry.charge_below, []).append(entry.name)
            elif self._window_not_ended(entry, now):
                windows.setdefault((entry.chargingStart, entry.chargingStop), []).append(entry.name)
            else:
                waiting.append(entry.name)
        return windows, waiting, below

    def _info_text(self, windows: dict, waiting: List[str], below: dict, now: datetime) -> str:
        """ For example 'Car A, Car B 01:00-04:00; Car C waiting for prices; Charge below 0.8: Car D'.
            A window that starts a day or more from now gets the weekday in front. """

        parts: List[str] = []
        for (start, stop), names in sorted(windows.items()):
            day = start.strftime("%a ") if start - now >= timedelta(days = 1) else ""
            parts.append(f"{', '.join(names)} {day}{start.strftime('%H:%M')}-{stop.strftime('%H:%M')}")
        if waiting:
            parts.append(f"{', '.join(waiting)} waiting for prices")
        for price, names in sorted(below.items()):
            parts.append(f"Charge below {price:g}: {', '.join(names)}")
        return self._shorten("; ".join(parts) or INFOTEXT_NOTHING_PLANNED, INFOTEXT_MAX_LENGTH)

    def _shorten(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[:limit - 1].rstrip(" ,;:") + "…"

    def _infotext_limit(self) -> int:
        """ The helper's own `max`: a new input_text allows 100 characters, and HA rejects a longer value. """

        helper_max = to_float_or_none(self.ADapi.get_state(self.infotext, attribute = 'max', namespace = self.namespace))
        if helper_max is None or helper_max < 1:
            return INFOTEXT_MAX_LENGTH
        return min(int(helper_max), INFOTEXT_MAX_LENGTH)

    def _mark_informed(self, now: datetime) -> bool:
        """ Remembers the plan of every queue entry. True when an entry has a plan the owner was not told. """

        new_info = False
        for entry in self.chargingQueue:
            if entry.charge_below != 0:
                if entry.informedBelow != entry.charge_below:
                    entry.informedBelow = entry.charge_below
                    new_info = True
            elif self._window_not_ended(entry, now):
                if entry.informedStart != entry.chargingStart or entry.informedStop != entry.chargingStop:
                    entry.informedStart = entry.chargingStart
                    entry.informedStop = entry.chargingStop
                    new_info = True
        return new_info

    def notifyChargeTime(self, kwargs) -> None:
        """ Writes the plan to the `infotext` helper, when configured, and sends it to `notify_receiver`.

            The helper always shows the plan as it is now. A notification goes out only for a new or
            changed plan: not when a car has finished and left the queue, and not after a restart or
            a re-plan that ends with the same plan. """

        now = self.ADapi.datetime(aware=True)
        windows, waiting, below = self._current_plan(now)
        info_text = self._info_text(windows, waiting, below, now)
        new_info = self._mark_informed(now)
        plan_changed = (windows, waiting, below) != self._last_plan
        self._last_plan = (windows, waiting, below)
        self.ADapi.log(f"Charge info: new {new_info}, changed {plan_changed}, helper {self.infotext}, {len(info_text)} chars: {info_text}", level = 'DEBUG') ###

        if self.infotext is not None:
            self.ADapi.call_service(
                "input_text/set_value",
                value = self._shorten(info_text, self._infotext_limit()),
                entity_id = self.infotext,
                namespace = self.namespace,
            )

        if new_info and plan_changed and self.notify_app is not None:
            self.notify_app.send_notification(
                message = info_text,
                message_title = "🔋 Charge Queue",
                message_recipient = self.recipients,
                also_if_not_home = True,
                data = {"tag": "chargequeue"},
            )
