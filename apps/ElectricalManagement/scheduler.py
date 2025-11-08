from __future__ import annotations

import bisect
from typing import Iterable, List, Optional, Tuple

# Local imports – adjust the module names to your actual project layout
from pydantic_models import ChargingQueueItem, WattSlot
from utils import get_next_runtime_aware

class Scheduler:
    """ Class for calculating and schedule charge times """

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

        self.simultaneousChargeComplete: list[str] = []
        self.currentlyCharging: set[str] = set()
        self.informHandler = None

        # helper values
        now = self.ADapi.datetime(aware=True)
        self.save_endHour = now.replace(minute=0, second=0, microsecond=0)

    def _calculate_expected_chargetime(
        self,
        kWhRemaining: float = 2,
        totalW_AllChargers: float = 3600,
        start_time: Optional[self.ADapi.datetime(aware=True)] = None,
    ) -> float:
        """ Estimate the *number of hours* it will take to finish a charge """

        if start_time is None:
            start_time = self.ADapi.datetime(aware=True)

        if start_time > self.save_endHour:
            self.save_endHour = get_next_runtime_aware(
                startTime = start_time, offset_seconds=0, delta_in_seconds=60 * 15
            )

        idx_start = bisect.bisect_left([s.start for s in self.available_watt], self.save_endHour)

        wh_remaining = kWhRemaining * 1_000
        hours_to_charge = 0.0

        for slot in self.available_watt[idx_start:]:
            if wh_remaining <= 0:
                break

            usable_wh = min(
                slot.available_Wh,
                totalW_AllChargers * slot.duration_hours,
            )

            if wh_remaining <= usable_wh:
                hours_to_charge += slot.duration_hours
                wh_remaining = 0
            else:
                wh_remaining -= usable_wh
                hours_to_charge += slot.duration_hours

        if wh_remaining > 0 and self.available_watt:
            last = self.available_watt[-1]
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
        according to the priority stored in `self._persistence.chargingQueue` """

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

        if (
            self.ADapi.now_is_between("09:00:00", "14:00:00")
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
            if max_price == -1:
                return False
        return self.electricalPriceApp.electricity_price_now() <= max_price


    def _update_prices_for_future_hours(self) -> float:
        """ When tomorrow's price data is not yet available """

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
            self.ADapi.log(f"Calculated est hours to charge: {est_hours} in _update_prices_for_future_hours") ###
        else:
            est_hours = sum(c.estHourCharge for c in self.chargingQueue if c.estHourCharge)

        price = self.electricalPriceApp.get_lowest_prices(
            checkitem = now.hour,
            hours = est_hours,
            min_change = 0.1
        )

        for c in self.chargingQueue:
            c.price = price
        return price

    def getVehiclePrice(self, vehicle_id: Optional[str] = None) -> float:
        """ Return the price for a specific vehicle if it is present in the queue.
        Otherwise return the *highest* price seen across all entries """

        highest_price = 0.0
        for entry in self.chargingQueue:
            if vehicle_id is not None and entry.vehicle_id == vehicle_id:
                return entry.price or 0.0
            if entry.price is not None and entry.price > highest_price:
                highest_price = entry.price
        return highest_price

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

    def removeFromQueue(self, vehicle_id: str) -> None:
        """ Remove the first queue entry that matches *vehicle_id* """

        for idx, entry in enumerate(self.chargingQueue):
            if entry.vehicle_id == vehicle_id:
                del self.chargingQueue[idx]
                break

    def queueForCharging(
        self,
        vehicle_id: str,
        kWhRemaining: float,
        maxAmps: int,
        voltPhase: int,
        finish_by_hour: int,
        priority: int,
        name: str,
    ) -> bool:
        """ Enqueue a new charging job (or replace an existing one) """

        self.removeFromQueue(vehicle_id)

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
        )
        self.chargingQueue.append(new_item)

        if self.ADapi.now_is_between("09:00:00", "14:00:00") and not self.electricalPriceApp.tomorrow_valid:
            return self.isChargingTime(vehicle_id=vehicle_id)

        self.process_charging_queue()
        return self.isChargingTime(vehicle_id=vehicle_id)

    def process_charging_queue(self) -> None:
        """ Resolve the whole queue, scheduling charging windows, detecting
        simultaneous sessions and finally computing the “best” price block
        for each job """

        self.chargingQueue.sort(key=lambda c: c.finish_by_hour)

        simultaneous_charge: List[str] = []
        self.simultaneousChargeComplete = []

        for i, current_car in enumerate(self.chargingQueue):
            (
                current_car.chargingStart,
                current_car.estimateStop,
                current_car.chargingStop,
                current_car.price,
            ) = self.electricalPriceApp.get_Continuous_Cheapest_Time(
                hoursTotal=current_car.estHourCharge,
                calculateBeforeNextDayPrices=False,
                finishByHour=current_car.finish_by_hour,
                startBeforePrice=self.startBeforePrice,
                stopAtPriceIncrease=self.stopAtPriceIncrease,
            )

            has_overlap = False
            for overlapping_id in simultaneous_charge:
                idx = next(
                    (j for j, c in enumerate(self.chargingQueue) if c.vehicle_id == overlapping_id),
                    None,
                )
                if idx is not None and self.chargingQueue[idx].chargingStop > current_car.chargingStart:
                    has_overlap = True
                    break

            if not has_overlap:
                for j in range(i - 1, -1, -1):
                    prev = self.chargingQueue[j]
                    if prev.chargingStop is not None and current_car.chargingStart < prev.chargingStop:
                        simultaneous_charge.append(prev.vehicle_id)
                simultaneous_charge.append(current_car.vehicle_id)
            else:
                simultaneous_charge.append(current_car.vehicle_id)

            next_index = i + 1
            if next_index < len(self.chargingQueue):
                next_car = self.chargingQueue[next_index]
                if next_car.chargingStart is not None and current_car.chargingStop is not None:
                    if next_car.chargingStart >= current_car.chargingStop:
                        if simultaneous_charge:
                            self.calcSimultaneousCharge(simultaneous_charge)
                            self.simultaneousChargeComplete.extend(simultaneous_charge)
                            simultaneous_charge = []

        if simultaneous_charge:
            self.calcSimultaneousCharge(simultaneous_charge)
            self.simultaneousChargeComplete.extend(simultaneous_charge)

    def calcSimultaneousCharge(self, simultaneous_charge: List[str]) -> None:
        """ Re-calculate the charging window for a group of vehicles that must run
        at the same time.  The function updates the queue in place """

        finish_by_hour = 0
        kWh_to_charge = 0.0
        total_w_all_chargers = 0.0
        start_time = self.ADapi.datetime(aware=True)

        for c in self.chargingQueue:
            if c.vehicle_id in simultaneous_charge:
                kWh_to_charge += c.kWhRemaining
                total_w_all_chargers += c.maxAmps * c.voltPhase

                if c.finish_by_hour > finish_by_hour:
                    if finish_by_hour == 0:
                        finish_by_hour = c.finish_by_hour
                    else:
                        finish_by_hour += c.estHourCharge

                if c.chargingStart is not None:
                    start_time = c.chargingStart

        hours_to_charge = self._calculate_expected_chargetime(
            kWhRemaining=kWh_to_charge,
            totalW_AllChargers=total_w_all_chargers,
            start_time=start_time,
        )

        (
            charging_at,
            estimate_stop,
            charging_stop,
            price,
        ) = self.electricalPriceApp.get_Continuous_Cheapest_Time(
            hoursTotal=hours_to_charge,
            calculateBeforeNextDayPrices=False,
            finishByHour=finish_by_hour,
            startBeforePrice=self.startBeforePrice,
            stopAtPriceIncrease=self.stopAtPriceIncrease,
        )

        if estimate_stop is not None:
            for c in self.chargingQueue:
                if c.vehicle_id in simultaneous_charge:
                    c.chargingStart = charging_at
                    c.estimateStop = estimate_stop
                    c.chargingStop = charging_stop
                    c.price = price

    def notifyChargeTime(self, kwargs) -> None:
        """ Sends notifications and updates infotext with charging times and prices """

        def _fmt(dt: datetime | None) -> str:
            """ Return a human readable string without the TZ component """
            return "" if dt is None else dt.strftime("%Y-%m-%d %H:%M")

        lowest_price: float | None = None
        times_set = False
        send_new_info = False
        info_text = ""
        info_text_simultaneous_car = "Charge "
        info_text_simultaneous_time = ""

        sorted_queue: List[ChargingQueueItem] = sorted(
            self.chargingQueue, key=lambda c: c.finish_by_hour
        )

        for car in sorted_queue:
            if self.charging_scheduled_with_updated_data(
                vehicle_id=car.vehicle_id,
                kWhRemaining=car.kWhRemaining,
                finish_by_hour=car.finish_by_hour,
            ):
                already_informed = (
                    car.informedStart is not None
                    and car.informedStop is not None
                    and car.chargingStart is not None
                )

                if already_informed:
                    if (
                        car.informedStart != car.chargingStart
                        or car.informedStop != car.estimateStop
                    ):
                        send_new_info = True
                else:
                    send_new_info = True

                if car.chargingStart is not None:
                    car.informedStart = car.chargingStart
                    car.informedStop = car.estimateStop

                    timestr_start = _fmt(car.chargingStart)
                    timestr_eta_stop = _fmt(car.estimateStop)
                    timestr_stop = _fmt(car.chargingStop)

                    if car.vehicle_id in self.simultaneousChargeComplete:
                        info_text_simultaneous_car += f"{car.name} & "
                        info_text_simultaneous_time = (
                            f" at {timestr_start}. Finish est at {timestr_eta_stop}. "
                            f"Stop no later than {timestr_stop}. "
                        )
                    else:
                        info_text += (
                            f"Start {car.name} at {timestr_start}. "
                            f"Finish est at {timestr_eta_stop} with {car.estHourCharge} hours to charge. "
                            f"Stop no later than {timestr_stop}. "
                        )
                    times_set = True

            if car.price is not None:
                if lowest_price is None or car.price < lowest_price:
                    lowest_price = car.price

        if info_text_simultaneous_car.endswith(" & "):
            info_text_simultaneous_car = info_text_simultaneous_car[:-3]

        info_text += info_text_simultaneous_car + info_text_simultaneous_time

        if not times_set and lowest_price is not None:
            price_msg = (
                f"Charge if price is lower than {self.electricalPriceApp.currency} "
                f"{round(lowest_price - self.electricalPriceApp.current_daytax, 3)} (day) or "
                f"{self.electricalPriceApp.currency} {round(lowest_price - self.electricalPriceApp.current_nighttax, 3)} (night/weekend)"
            )
            info_text = price_msg

        self.ADapi.log(info_text) ###

        if self.infotext not in (None, "Charge "):
            info_text.strip()
            self.ADapi.call_service(
                "input_text/set_value",
                value = info_text,
                entity_id = self.infotext,
                namespace = self.namespace,
            )

            if send_new_info:
                data = {"tag": "chargequeue"}
                self.notify_app.send_notification(
                    message = info_text,
                    message_title = "🔋 Charge Queue",
                    message_recipient = self.recipients,
                    also_if_not_home = True,
                    data = data,
                )
