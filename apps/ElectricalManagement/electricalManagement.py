""" ElectricalManagement.

    @Pythm / https://github.com/Pythm
"""

__version__ = "0.2.6"

from appdaemon import adbase as ad
import datetime
import math
import json
import csv
import inspect

RECIPIENTS:list = []
NOTIFY_APP = None
JSON_PATH:str = ''
OUT_TEMP:float = 10.0
RAIN_AMOUNT:float = 0.0
WIND_AMOUNT:float = 0.0


class ElectricityPrice:

    def __init__(self, api,
        nordpool,
        daytax:float,
        nighttax:float,
        workday,
        power_support_above:float,
        support_amount:float
    ):

        self.ADapi = api
        self.nordpool_prices = None
        self.nordpool_last_updated = self.ADapi.datetime(aware=True)
        if nordpool:
            self.nordpool_prices = nordpool
        else:
            sensor_states = self.ADapi.get_state()
            for sensor_id, sensor_states in sensor_states.items():
                if 'nordpool' in sensor_id:
                    self.nordpool_prices = sensor_id
                    break
        if not self.nordpool_prices:
            raise Exception(
                "Nordpool custom components not found. Please install Nordpool via HACS: https://github.com/custom-components/nordpool"
            )

        self.currency:str = self.ADapi.get_state(entity_id = self.nordpool_prices, attribute = 'currency')
        self.daytax:float = daytax
        self.nighttax:float = nighttax
        self.workday = workday

        self.power_support_above:float = power_support_above
        self.support_amount:float = support_amount

        self.elpricestoday:list = []
        self.nordpool_todays_prices:list = []
        self.nordpool_tomorrow_prices:list = []
        self.sorted_elprices_today:list = []
        self.sorted_elprices_tomorrow:list = []

        self.getprices()
        self.ADapi.listen_state(self.update_price_rundaily, self.nordpool_prices,
            attribute = 'tomorrow'
        )


    def update_price_rundaily(self, entity, attribute, old, new, kwargs) -> None:
        """ Calls getprices() on sensor change.
        """
        self.getprices()


    def getprices(self) -> None:
        """ Fetches prices from Nordpool sensor and adds day and night tax.
            
            TODO: Verify time with attributes from "Raw today" and "Raw tomorrow" containing datetime
            Fail every time summertime is starting/stopping due to one hour less/more.
        """
        self.elpricestoday = []
        isNotWorkday:bool = self.ADapi.get_state(self.workday) == 'off'

        # Todays prices
        try:
            self.nordpool_todays_prices = self.ADapi.get_state(entity_id = self.nordpool_prices, attribute = 'today')
            for hour in range(0,len(self.nordpool_todays_prices)):
                calculated_support:float = 0.0 # Power support calculation
                if float(self.nordpool_todays_prices[hour]) > self.power_support_above:
                    calculated_support = (float(self.nordpool_todays_prices[hour]) - self.power_support_above ) * self.support_amount
                if (
                    hour < 6
                    or hour > 21
                    or datetime.datetime.today().weekday() > 4
                    or isNotWorkday
                ):
                    self.elpricestoday.insert(hour, round(float(self.nordpool_todays_prices[hour]) + self.nighttax - calculated_support, 3))
                else:
                    self.elpricestoday.insert(hour, round(float(self.nordpool_todays_prices[hour]) + self.daytax - calculated_support, 3))

        except Exception as e:
            self.ADapi.log(f"Nordpool prices today failed. Exception: {e}", level = 'DEBUG')
            self.ADapi.run_in(self.getprices, 1800)
            self.sorted_elprices_today = []
        else:
            self.sorted_elprices_today = sorted(self.elpricestoday)


        # Tomorrows prices if available
        if self.ADapi.get_state(entity_id = self.nordpool_prices, attribute = 'tomorrow_valid'):
            try:
                self.nordpool_tomorrow_prices = self.ADapi.get_state(entity_id = self.nordpool_prices, attribute = 'tomorrow')
                if (
                    len(self.nordpool_tomorrow_prices) > 0
                    and self.nordpool_todays_prices != self.nordpool_tomorrow_prices
                ):
                    for hour in range(0,len(self.nordpool_tomorrow_prices)):
                        calculated_support:float = 0.0 # Power support calculation
                        if float(self.nordpool_tomorrow_prices[hour]) > self.power_support_above:
                            calculated_support = (float(self.nordpool_tomorrow_prices[hour]) - self.power_support_above ) * self.support_amount

                        """
                            TODO: Does not check if tomorrow is holiday when applying day or night tax to tomorrows prices. 
                        """

                        if (
                            hour < 6
                            or hour > 21
                            or datetime.datetime.today().weekday() == 4
                            or datetime.datetime.today().weekday() == 5
                        ):
                            self.elpricestoday.insert(
                                hour+24, round(float(self.nordpool_tomorrow_prices[hour]) + self.nighttax - calculated_support, 3)
                            )
                        else:
                            self.elpricestoday.insert(
                                hour+24, round(float(self.nordpool_tomorrow_prices[hour]) + self.daytax - calculated_support, 3)
                            )
                        self.sorted_elprices_tomorrow.insert(hour, self.elpricestoday[hour+24])

            except IndexError as ie:
                self.nordpool_tomorrow_prices = self.ADapi.get_state(entity_id = self.nordpool_prices, attribute = 'tomorrow')
                self.sorted_elprices_tomorrow = []

                for hour in range(0,len(self.nordpool_tomorrow_prices)-1):
                    calculated_support:float = 0.0 # Power support calculation
                    if float(self.nordpool_tomorrow_prices[hour]) > self.power_support_above:
                        calculated_support = (float(self.nordpool_tomorrow_prices[hour]) - self.power_support_above ) * self.support_amount
                    if (
                        hour < 6
                        or hour > 21
                        or datetime.datetime.today().weekday() == 4
                        or datetime.datetime.today().weekday() == 5
                    ):
                        self.elpricestoday.insert(
                            hour+24, round(float(self.nordpool_tomorrow_prices[hour]) + self.nighttax - calculated_support, 3)
                        )
                    else:
                        self.elpricestoday.insert(
                            hour+24, round(float(self.nordpool_tomorrow_prices[hour]) + self.daytax - calculated_support, 3)
                        )
                    self.sorted_elprices_tomorrow.insert(hour, self.elpricestoday[hour+24])
                self.sorted_elprices_tomorrow = sorted(self.sorted_elprices_tomorrow)

            else:
                self.sorted_elprices_tomorrow = sorted(self.sorted_elprices_tomorrow)


    def getContinuousCheapestTime(self,
        hoursTotal:int,
        calculateBeforeNextDayPrices:bool,
        finishByHour:int
    ) -> (datetime, datetime, float):
        """ Returns starttime, endtime and price for cheapest continuous hours with different options depenting on time the call was made.
        """
        finishByHour += 1
        h = math.floor(hoursTotal)
        if h == 0:
            h = 1
        if (
            self.ADapi.now_is_between('13:00:00', '23:59:59')
            and len(self.elpricestoday) >= 47 # Day starting summertime only has 47 hours
        ):
            finishByHour += 24
        elif (
            self.ADapi.now_is_between('06:00:00', '15:00:00')
            and len(self.elpricestoday) == 24
        ):
            if not calculateBeforeNextDayPrices:
                return None, None, self.sorted_elprices_today[h]
        elif (
            self.ADapi.now_is_between('15:00:00', '23:59:59')
            and len(self.elpricestoday) < 47
            and self.ADapi.datetime(aware=True) - self.nordpool_last_updated > datetime.timedelta(minutes = 30)
        ):
            """ It can happen that the Nordpool does not update properly with tomorrows prices.
                That has not been tested properly so I'm not sure reloading intergration works.
                One time I had to restart HA for Nordpool integration to get tomorrows prices.
                TODO: Find out what data to trigger reload of Nordpool integration.
            """
            self.nordpool_last_updated = self.ADapi.datetime(aware=True)
            self.ADapi.log(
                "RELOADS Nordpool integration. Is tomorrows prices valid? "
                f"{self.ADapi.get_state(entity_id = self.nordpool_prices, attribute = 'tomorrow_valid')} : "
                f"{self.ADapi.get_state(entity_id = self.nordpool_prices, attribute = 'tomorrow')}",
                level = 'WARNING'
            )

            self.ADapi.call_service('homeassistant/reload_config_entry',
                entity_id = self.nordpool_prices
            )

        # Daytime savings Time transition:
        if finishByHour > len(self.elpricestoday):
            finishByHour = len(self.elpricestoday)

        priceToComplete:float = 0.0
        avgPriceToComplete:float = 999.99
        startTime:int = datetime.datetime.today().hour
        start_of_range:int = startTime
        if h < finishByHour - start_of_range:
            for check in range(start_of_range, finishByHour - h):
                for hour in range(check, check + h):
                    priceToComplete += self.elpricestoday[hour]
                if priceToComplete < avgPriceToComplete:
                    avgPriceToComplete = priceToComplete
                    startTime = check
                priceToComplete = 0.0
        elif start_of_range < finishByHour:
            divide:int = 0
            for hour in range(start_of_range, finishByHour ):
                priceToComplete += self.elpricestoday[hour]
                divide += 1
            avgPriceToComplete = priceToComplete / divide

        runtime = datetime.datetime.today().replace(hour = 0, minute = 0, second = 0, microsecond = 0 ) + datetime.timedelta(hours = startTime)
        endtime = runtime + datetime.timedelta(hours = hoursTotal)
        if runtime.hour == datetime.datetime.today().hour:
            runtime = datetime.datetime.today().replace(second=0, microsecond=0)
        return runtime, endtime, round(avgPriceToComplete/h, 3)


    def findlowprices(self,
        checkhour:int = 1,
        hours:int = 6,
        min_change:float = 0.1
    ) -> float:
        """ Helper function that compares the X hour lowest price to a minimum change and retuns the highest price of those two.
        """
        hours -= 1 # Lists operates 0-23
        if checkhour < 24:
            if self.sorted_elprices_today[hours] > self.sorted_elprices_today[0] + min_change:
                return self.sorted_elprices_today[hours]
            else:
                return self.sorted_elprices_today[0] + min_change
        else:
            if self.sorted_elprices_tomorrow[hours] > self.sorted_elprices_tomorrow[0] + min_change:
                return self.sorted_elprices_tomorrow[hours]
            else:
                return self.sorted_elprices_tomorrow[0] + min_change


    def findpeakhours(self,
        pricedrop:float,
        max_continuous_hours:int,
        on_for_minimum:int,
        pricedifference_increase:float,
        reset_continuous_hours:bool,
        prev_peak_hours:list
    ) -> (list, int, int, list):
        """ Finds peak variations in electricity price for saving purposes and returns list with datetime objects,
            int with continious hours off, and int with hour turned back on for when to save.
        """
        peak_hours = []
        hour = datetime.datetime.now().hour
        stop_hour_from_previous_peak:int = hour + 1
        continuous_hours_from_prev:int = 0
        length = len(self.elpricestoday) -1
        if length > 24:
            peak_hours = prev_peak_hours.copy()
            if prev_peak_hours:
                for h in sorted(prev_peak_hours):
                    if h + 1 in prev_peak_hours:
                        continuous_hours_from_prev += 1
                    elif continuous_hours_from_prev > 1:
                        continuous_hours_from_prev -= 2
                    elif continuous_hours_from_prev == 1:
                        continuous_hours_from_prev = 0
                    if h >= hour:
                        break

        while hour < length:
                # Checks if price drops more than wanted peak difference
            if (
                self.elpricestoday[hour] - self.elpricestoday[hour+1] >= pricedrop
                and hour not in peak_hours
            ):
                if self.elpricestoday[hour] > self.findlowprices(checkhour = hour, hours = on_for_minimum):
                    peak_hours.append(hour)
                else:
                    countDown = on_for_minimum - 1
                    h = hour +1
                    while (
                        self.elpricestoday[hour] > self.elpricestoday[h]
                        and h < length
                        and countDown > 0
                    ):
                        h += 1
                        countDown -= 1
                    if countDown == 0:
                        peak_hours.append(hour)
            hour += 1
        
        if not peak_hours:
            hour = 0
            while hour < length -2:
                    # Checks if price drops 2x more than wanted peak difference during 3 hours
                if (
                    self.elpricestoday[hour] - self.elpricestoday[hour+3] >= pricedrop * 1.3
                    and self.elpricestoday[hour+1] > self.findlowprices(checkhour = hour, hours = on_for_minimum)
                ):
                    peak_hours.append(hour+2)
                hour += 1

        peaks_list = peak_hours.copy()
        if peak_hours:
                # Checks if price increases again during next 2 hours and removes peak
            for peak in peaks_list:
                if peak < len(self.elpricestoday)-2:
                    if (
                        (self.elpricestoday[peak] < self.elpricestoday[peak+1]
                        or self.elpricestoday[peak] < self.elpricestoday[peak+2])
                        and peak not in prev_peak_hours
                    ):
                        peak_hours.remove(peak)

        continuous_hours:int = 0
        highest_continuous_hours:int = 0
        if peak_hours:
            peak_hours = sorted(peak_hours)
            # Finds continuous more expencive hours before peak hour
            list_of_hours_after_price_decrease:list = []
            peaks_list = peak_hours.copy()

            neg_peak_counter_hour = len(peak_hours) -1
            first_peak_hour = peaks_list[0]
            last_hour = peaks_list[-1]

            while (
                last_hour >= first_peak_hour
                and last_hour >= stop_hour_from_previous_peak
                and neg_peak_counter_hour >= 0
            ):
                if not last_hour in peak_hours:
                    if highest_continuous_hours < continuous_hours:
                        highest_continuous_hours = continuous_hours
                    continuous_hours = 0
                peakdiff = pricedrop
                last_hour = peaks_list[neg_peak_counter_hour]

                h = last_hour 
                hour_list = []
                # Count up while hours in peak.
                while (
                    neg_peak_counter_hour >= 0
                    and last_hour == peaks_list[neg_peak_counter_hour]
                    and last_hour >= first_peak_hour
                    and last_hour >= stop_hour_from_previous_peak
                ):
                    neg_peak_counter_hour -= 1
                    last_hour -= 1
                    continuous_hours += 1

                continuous_hours_first_while = continuous_hours
                # Count backwards while price is high.
                while (
                    self.elpricestoday[last_hour] > self.elpricestoday[h+1] + peakdiff
                    and last_hour > stop_hour_from_previous_peak
                    and last_hour < 39 # Only until 14.00 next day.
                    and continuous_hours_first_while < max_continuous_hours
                ):
                    if (
                        not last_hour in peak_hours
                        and not last_hour-1 in peak_hours
                        and not last_hour-2 in peak_hours
                    ):
                        hour_list.append(last_hour)
                        last_hour -= 1
                        continuous_hours_first_while += 1
                        continuous_hours += 1
                        peakdiff *= pricedifference_increase # Adds a x% increase in pricedifference pr hour saving.

                    else:
                        continuous_hours_second_while = 0

                        while (
                            continuous_hours_second_while < max_continuous_hours
                            and self.elpricestoday[last_hour] > self.elpricestoday[h+1] + peakdiff
                            and last_hour > stop_hour_from_previous_peak
                        ):
                            hour_list.append(last_hour)
                            if last_hour in peaks_list:
                                neg_peak_counter_hour -= 1
                                continuous_hours_second_while = 0
                                if (
                                    not last_hour +1 in peak_hours
                                    and not last_hour +1 in list_of_hours_after_price_decrease
                                ):
                                    list_of_hours_after_price_decrease.append(last_hour+1)
                                if (
                                    not last_hour +2 in peak_hours
                                    and not last_hour+2 in list_of_hours_after_price_decrease
                                ):
                                    list_of_hours_after_price_decrease.append(last_hour+2)

                            peakdiff *= pricedifference_increase
                            last_hour -= 1
                            continuous_hours += 1
                            continuous_hours_second_while += 1

                        last_hour -= 1

                if last_hour <= stop_hour_from_previous_peak:
                    continuous_hours += continuous_hours_from_prev
                    continuous_hours_from_prev = 0

                for num in reversed(hour_list):
                    if not num in peak_hours:
                        peak_hours.append(num)

            peak_hours = sorted(peak_hours)
            if continuous_hours > max_continuous_hours:
                # Calculated more save hours than allowed by configuration. Find cheaper hours to remove.

                least_expencive_after_peak_hour:float = 1000
                least_expencive_hour:int = 0
                was_able_to_remove_in_price_check:bool = False
                continuous_hours_to_remove = continuous_hours - max_continuous_hours

                # Find the least expencive hour in peak_hour.
                for hour in list_of_hours_after_price_decrease:
                    if self.elpricestoday[hour] < least_expencive_after_peak_hour:
                        least_expencive_after_peak_hour = self.elpricestoday[hour]
                        least_expencive_hour = hour

                # Find hours with cheaper prices.
                list_with_cheaper_prices:list = []
                list_with_hours_cheaper_prices:list = []
                for hour in peak_hours:
                    if (
                        self.elpricestoday[hour] < least_expencive_after_peak_hour
                        and hour < least_expencive_hour
                    ):
                        was_able_to_remove_in_price_check = True
                        list_with_cheaper_prices.append(self.elpricestoday[hour])
                        list_with_hours_cheaper_prices.append(hour)

                # If hours with cheaper prices found then remove enough to stay below max continuous hours.
                if len(list_with_hours_cheaper_prices) >= continuous_hours_to_remove:
                    list_with_cheaper_prices = sorted(list_with_cheaper_prices)
                    price_check = list_with_cheaper_prices[continuous_hours_to_remove -1]

                    list_with_hours_cheaper_prices_copy = list_with_hours_cheaper_prices.copy()
                    for hour in list_with_hours_cheaper_prices_copy:
                        if self.elpricestoday[hour] > price_check:
                            list_with_hours_cheaper_prices.remove(hour)

                for remove_hour in list_with_hours_cheaper_prices:
                    if remove_hour in peak_hours:
                        peak_hours.remove(remove_hour)
                        continuous_hours -= 1

                if continuous_hours > max_continuous_hours:
                    continuous_peak_hours = peak_hours.copy()
                    initial_hour = 0
                    end_hour = 0
                    for hour in continuous_peak_hours:
                        if (
                            initial_hour == 0
                            and hour + 1 in continuous_peak_hours
                        ):
                            initial_hour = hour
                        elif (
                            initial_hour != 0
                            and hour in continuous_peak_hours
                            and hour - 1 in continuous_peak_hours
                        ):
                            end_hour = hour
                        elif hour > 26:
                            break
                        elif (
                            initial_hour != 0
                            and end_hour != 0
                        ):
                            if reset_continuous_hours:
                                continuous_hours = end_hour - initial_hour +1
                                if continuous_hours > max_continuous_hours:
                                    peak_hours, continuous_hours = self.remove_hours_from_peak_hours(
                                        peak_hours = peak_hours,
                                        pricedrop = pricedrop,
                                        pricedifference_increase = pricedifference_increase,
                                        max_continuous_hours = max_continuous_hours,
                                        continuous_hours = continuous_hours,
                                        initial_hour = initial_hour,
                                        end_hour = end_hour
                                    )
                                initial_hour = hour
                    if (
                        initial_hour != 0
                        and end_hour != 0
                    ):
                        peak_hours, continuous_hours = self.remove_hours_from_peak_hours(
                            peak_hours = peak_hours,
                            pricedrop = pricedrop,
                            pricedifference_increase = pricedifference_increase,
                            max_continuous_hours = max_continuous_hours,
                            continuous_hours = continuous_hours,
                            initial_hour = initial_hour,
                            end_hour = end_hour
                        )

        if highest_continuous_hours < continuous_hours:
            highest_continuous_hours = continuous_hours
        elif highest_continuous_hours > max_continuous_hours:
            highest_continuous_hours = max_continuous_hours

        peak_times = []
        turn_on_at:int = 0
        for t in peak_hours:
            peak_times.append(
                datetime.datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
                + datetime.timedelta(hours = t)
            )
            if (
                not t+1 in peak_hours
                and t < 26
            ):
                turn_on_at = t+1

        return peak_times, highest_continuous_hours, turn_on_at, peak_hours


    def remove_hours_from_peak_hours(self,
        peak_hours:list,
        pricedrop:float,
        pricedifference_increase:float,
        max_continuous_hours:int,
        continuous_hours:int,
        initial_hour:int,
        end_hour:int
    ) -> (list, int):
        """ Finds hours to remove based on pricedrop and pricedifference_increase between the first and last hours.
        """

        if initial_hour <= datetime.datetime.now().hour:
            initial_hour = datetime.datetime.now().hour                      
        while (
            continuous_hours > max_continuous_hours
        ):
            start_pricedrop:float = self.calculate_difference_over_given_time(
                pricedrop = pricedrop,
                multiplier = pricedifference_increase,
                iterations = end_hour - initial_hour
            )
            if (
                self.elpricestoday[initial_hour] + start_pricedrop > self.elpricestoday[end_hour] + pricedrop
            ):
                peak_hours.remove(end_hour)
                end_hour -= 1
            elif initial_hour in peak_hours:
                peak_hours.remove(initial_hour)
                initial_hour += 1
            continuous_hours -= 1
            if end_hour == initial_hour:
                break

        return peak_hours, continuous_hours


    def calculate_difference_over_given_time(self,
        pricedrop: float,
        multiplier: float,
        iterations: int
    ) -> float:
        """ Calculates the difference after having multiplied the initial value with a given factor
        """
        start_hour_price = pricedrop * (multiplier ** iterations)
        return start_hour_price


    def findLowPriceHours(self,
        priceincrease:float,
        max_continuous_hours:int
    ) -> list:
        """ Finds low price variations in electricity price for spending purposes and returns list with datetime objects.
        """
        cheap_hours = []
        hour = 1
        length = len(self.elpricestoday) -2

        while hour < length:
                # Checks if price increases more than wanted peak difference
            if (
                self.elpricestoday[hour+1] - self.elpricestoday[hour] >= priceincrease
                and self.elpricestoday[hour] <= self.findlowprices(hour, 3, 0.08)
            ):
                cheap_hours.append(hour)
                if self.elpricestoday[hour-1] < self.elpricestoday[hour] + 0.06:
                    cheap_hours.append(hour-1)
                hour += 1
                # Checks if price increases x1,4 peak difference during two hours
            elif (
                self.elpricestoday[hour+1] - self.elpricestoday[hour] >= (priceincrease * 0.6)
                and self.elpricestoday[hour+1] - self.elpricestoday[hour-1] >= (priceincrease * 1.4)
                and self.elpricestoday[hour-1] <= self.findlowprices(hour, 3, 0.1)
            ):
                cheap_hours.append(hour-1)
                if self.elpricestoday[hour-2] < self.elpricestoday[hour-1] + 0.06:
                    cheap_hours.append(hour-2)
            hour += 1
        cheap_hours = sorted(cheap_hours)
        cheap_times = []

        for t in cheap_hours:
            if not datetime.datetime.today().replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(hours = t) in cheap_times:
                cheap_times.append(datetime.datetime.today().replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(hours = t))
        return cheap_times


    def print_peaks(self,
        peak_hours:list = []
    ) -> None:
        """ Formats hours list to readable string for easy logging/testing of settings.
        """
        print_peak_hours:str = ''
        for t in peak_hours:
            if (
                t - datetime.timedelta(hours = 1) in peak_hours
                and t + datetime.timedelta(hours = 1) in peak_hours
            ):
                continue
            hour = (t - datetime.datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds() / 3600
            if hour < 24:
                print_peak_hours += str(f"{self.currency} {self.elpricestoday[int(hour)]} today at {int(hour)}")
            else:
                print_peak_hours += str(f"{self.currency} {self.elpricestoday[int(hour)]} tomorrow at {int(hour)-24}")
            if (
                not t - datetime.timedelta(hours = 1) in peak_hours
                and t + datetime.timedelta(hours = 1) in peak_hours
            ):
                print_peak_hours += " until "
            elif (
                t - datetime.timedelta(hours = 1) in peak_hours
                and not t + datetime.timedelta(hours = 1) in peak_hours
            ):
                if hour < 24:
                    print_peak_hours += str(
                        f". Goes back to normal at {int(hour)+1} {self.currency} {self.elpricestoday[int(hour)+1]}."
                    )
                else:
                    print_peak_hours += str(
                        f". Goes back to normal tomorrow at {int(hour)-23} {self.currency} {self.elpricestoday[int(hour)+1]}. "
                    )
            else:
                print_peak_hours += ". "
        return print_peak_hours



class ElectricalUsage(ad.ADBase):
    """ Main class of ElectricalManagement

        @Pythm / https://github.com/Pythm
    """

    def initialize(self):
        self.ADapi = self.get_ad_api()
            # Set a master namespace for all entities unless specified per entity
        self.HASS_namespace:str = self.args.get('main_namespace', 'default')

        self.chargers:list = []
        self.cars:list = []
        self.appliances:list = []
        self.heaters:list = []

            # Set up notification app
        global RECIPIENTS
        global NOTIFY_APP
        name_of_notify_app = self.args.get('notify_app', None)
        RECIPIENTS = self.args.get('notify_receiver', [])
        if name_of_notify_app != None:
            NOTIFY_APP = self.ADapi.get_app(name_of_notify_app)
        else:
            NOTIFY_APP = Notify_Mobiles(self.ADapi, self.HASS_namespace)

            # Set up workday sensor
        if 'workday' in self.args:
            workday_sensor = self.args['workday']
        elif self.ADapi.entity_exists('binary_sensor.workday_sensor', namespace = self.HASS_namespace):
            workday_sensor = 'binary_sensor.workday_sensor'
        else:
            workday_sensor = 'binary_sensor.workday_sensor_AD'
            if not self.ADapi.entity_exists(workday_sensor, namespace = self.HASS_namespace):
                self.ADapi.call_service("state/set",
                    entity_id = workday_sensor,
                    attributes = {'friendly_name' : 'Workday'},
                    state = 'on',
                    namespace = self.HASS_namespace
                )
                self.ADapi.log(
                    "'workday' binary_sensor not defined in app configuration or found in Home Assistant. "
                    "Will only use Saturdays and Sundays as nighttax and not Holidays. "
                    "Please install workday sensor from: https://www.home-assistant.io/integrations/workday/ "
                    "to calculate nighttime tax during hollidays",
                    level = 'INFO'
                )

            # Set up electricity price class
        global ELECTRICITYPRICE
        ELECTRICITYPRICE = ElectricityPrice(api = self.ADapi,
        nordpool = self.args.get('nordpool',None),
        daytax = self.args.get('daytax',0),
        nighttax = self.args.get('nighttax',0),
        workday = workday_sensor,
        power_support_above = self.args.get('power_support_above', 10),
        support_amount = self.args.get('support_amount', 0)
    )

            # Consumption sensors
        self.current_consumption = self.args.get('power_consumption', None) # Watt
        if not self.current_consumption:
            raise Exception (
                "power_consumption sensor not provided in configuration. Aborting Electrical Usage setup. "
                "Please provide a watt power consumption sensor to use this function"
            )
        try:
            float(self.ADapi.get_state(self.current_consumption))
        except ValueError as ve:
            if self.ADapi.get_state(self.current_consumption) == 'unavailable':
                self.ADapi.log(f"Current consumption is unavailable at startup", level = 'DEBUG')
            else:
                raise Exception ()
        except Exception as e:
            self.ADapi.log(
                f"power_consumption sensor is not a number. Please provide a watt power consumption sensor for this function",
                level = 'WARNING'
            )
            self.ADapi.log(
                "If power_consumption should be a number and this error occurs after a restart, "
                "your sensor has probably not started sending data.",
                level = 'INFO'
            )
            self.ADapi.log(e, level = 'DEBUG')

        sensor_states = None
        if 'accumulated_consumption_current_hour' in self.args:
            self.accumulated_consumption_current_hour = self.args['accumulated_consumption_current_hour'] # kWh
        else:
            sensor_states = self.ADapi.get_state(namespace = self.HASS_namespace)
            for sensor_id, sensor_states in sensor_states.items():
                if 'accumulated_consumption_current_hour' in sensor_id:
                    self.accumulated_consumption_current_hour = sensor_id
                    break

        if not self.accumulated_consumption_current_hour:
            try:
                raise Exception (
                    "accumulated_consumption_current_hour not found. "
                    "Please install Tibber Pulse or input equivialent to provide kWh consumption current hour."
                )
            except Exception:
                self.ADapi.log(
                    "Check out https://tibber.com/ to learn more. "
                    "If you are interested in switchin to Tibber you can use my invite link to get a startup bonus: "
                    "https://invite.tibber.com/fydzcu9t"
                    " or contact me for a invite code.",
                    level = 'INFO'
                )
        else:
            attr_last_updated = self.ADapi.get_state(
                entity_id=self.accumulated_consumption_current_hour,
                attribute="last_updated"
            )
            if not attr_last_updated:
                self.ADapi.log(
                    f"{self.ADapi.get_state(self.accumulated_consumption_current_hour)} has no 'last_updated' attribute. Function might fail",
                    level = 'INFO'
                )

            # Electricity power production sensors
        self.current_production = self.args.get('power_production', None) # Watt
        self.accumulated_production_current_hour = self.args.get('accumulated_production_current_hour', None) # Watt

            # Setting buffer for kWh usage
        self.buffer:float = self.args.get('buffer', 0.4)
        self.buffer += 0.02 # Added internal buffer correction to adjust based on how consumption is controlled
        self.max_kwh_goal:int = self.args.get('max_kwh_goal', 15)


        # Setting up charge scheduler
        global CHARGE_SCHEDULER
        CHARGE_SCHEDULER = Scheduler(api = self.ADapi,
            stopAtPriceIncrease = self.args.get('stopAtPriceIncrease', 0.3),
            startBeforePrice = self.args.get('startBeforePrice', 0.01),
            infotext = self.args.get('infotext', None),
            namespace = self.HASS_namespace
        )

        self.queueChargingList:list = [] # Cars currently charging.
        self.solarChargingList:list = [] # Cars currently charging.


            # Establish and recall persistent data using JSON
        global JSON_PATH
        JSON_PATH = self.args.get('json_path', None)
        self.json_path = JSON_PATH
        if not JSON_PATH:
            raise Exception (
                "Path to store json not provided. Please input a valid path with configuration 'json_path' "
            )
        ElectricityData:dict = {}

        try:
            with open(JSON_PATH, 'r') as json_read:
                ElectricityData = json.load(json_read)

                if 'chargingQueue' in ElectricityData:
                    completeQueue:list = []
                    for jsonQueue in ElectricityData['chargingQueue']:
                        if 'vehicle_id' in jsonQueue:
                            queue:dict = {}
                            for key, value in jsonQueue.items():
                                try:
                                    datevalue = self.ADapi.convert_utc(value)
                                    datevalue = datevalue.replace(tzinfo=None)
                                    queue.update({key: datevalue})
                                except Exception as e:
                                    queue.update({key : value})
                            completeQueue.append(queue)

                    CHARGE_SCHEDULER.chargingQueue = completeQueue

                if 'queueChargingList' in ElectricityData:
                    self.queueChargingList = ElectricityData['queueChargingList']
                if 'solarChargingList' in ElectricityData:
                    self.solarChargingList = ElectricityData['solarChargingList']

        except FileNotFoundError:
            ElectricityData = {"MaxUsage" : {"max_kwh_usage_pr_hour": self.max_kwh_goal, "topUsage" : [0,0,0]},
                            "charger" : {},
                            "car" : {},
                            "consumption" : {"idleConsumption" : {"ConsumptionData" : {}}}}
            with open(JSON_PATH, 'w') as json_write:
                json.dump(ElectricityData, json_write, indent = 4)
            self.ADapi.log(
                f"Json file created at {JSON_PATH}",
                level = 'INFO'
            )

        self.max_kwh_usage_pr_hour:int = ElectricityData['MaxUsage']['max_kwh_usage_pr_hour']
        newTotal:float = 0.0
        self.top_usage_hour:float = ElectricityData['MaxUsage']['topUsage'][0] # Lowest of top 3 consumption hours. Used to log, if higher.


            # Default vacation state for saving purposes when away from home for longer periodes
        if 'away_state' in self.args: # Old name...
            self.away_state = self.args['away_state']
        elif 'vacation' in self.args:
            self.away_state = self.args['vacation']
        else:
            self.away_state = 'input_boolean.vacation'
            if not self.ADapi.entity_exists(self.away_state, namespace = self.HASS_namespace):
                self.ADapi.call_service("state/set",
                    entity_id = self.away_state,
                    attributes = {'friendly_name' : 'Vacation'},
                    state = 'off',
                    namespace = self.HASS_namespace
                )
            else:
                self.ADapi.log(
                    "'vacation' not configured. Using 'input_boolean.vacation' as default away state",
                    level = 'INFO'
                )

        if 'automate' in self.args:
            self.automate = self.args['automate']
        else:
            self.automate = 'input_boolean.automate_heating'
            if not self.ADapi.entity_exists(self.automate, namespace = self.HASS_namespace):
                self.ADapi.call_service("state/set",
                    entity_id = self.automate,
                    attributes = {'friendly_name' : 'Automate Heating'},
                    state = 'on',
                    namespace = self.HASS_namespace
                )


            # Weather sensors
        self.outside_temperature = self.args.get('outside_temperature', None)

        self.rain_sensor = self.args.get('rain_sensor', None)
        self.rain_level:float = self.args.get('rain_level',3)
        self.anemometer = self.args.get('anemometer', None)
        self.anemometer_speed:int = self.args.get('anemometer_speed',40)
            # Setup Outside temperatures

        global OUT_TEMP
        self.out_temp_last_update = self.ADapi.datetime(aware=True) - datetime.timedelta(minutes = 20)
        if self.outside_temperature:
            self.ADapi.listen_state(self.outsideTemperatureUpdated, self.outside_temperature)
            try:
                OUT_TEMP = float(self.ADapi.get_state(self.outside_temperature))
            except (ValueError, TypeError):
                self.ADapi.log(f"Outside temperature is not valid. {e}", level = 'DEBUG')

            # Setup Rain sensor
        global RAIN_AMOUNT
        self.rain_last_update = self.ADapi.datetime(aware=True) - datetime.timedelta(minutes = 20)
        if self.rain_sensor:
            self.ADapi.listen_state(self.rainSensorUpdated, self.rain_sensor)
            try:
                RAIN_AMOUNT = float(self.ADapi.get_state(self.rain_sensor))
            except (ValueError) as ve:
                RAIN_AMOUNT = 0.0
                self.ADapi.log(f"Rain sensor not valid. {ve}", level = 'DEBUG')

            # Setup Wind sensor
        global WIND_AMOUNT
        self.wind_last_update = self.ADapi.datetime(aware=True) - datetime.timedelta(minutes = 20)
        if self.anemometer:
            self.ADapi.listen_state(self.anemometerUpdated, self.anemometer)
            try:
                WIND_AMOUNT = float(self.ADapi.get_state(self.anemometer))
            except (ValueError) as ve:
                WIND_AMOUNT = 0.0
                self.ADapi.log(f"Anemometer sensor not valid. {ve}", level = 'DEBUG')

        self.ADapi.listen_event(self.weather_event, 'WEATHER_CHANGE',
            namespace = self.HASS_namespace
        )

            # Set up chargers
        self.notify_overconsumption:bool = False
        self.pause_charging:bool = False
        if 'options' in self.args:
            if 'notify_overconsumption' in self.args['options']:
                self.notify_overconsumption = True
            if 'pause_charging' in self.args['options']:
                self.pause_charging = True


        # Setting up Tesla cars using Tesla API to control charging
        teslas = self.args.get('tesla', [])
        for t in teslas:
            namespace = t.get('namespace',self.HASS_namespace)
            charger_sensor = t.get('charger_sensor',None)
            charger_switch = t.get('charger_switch',None)
            charging_amps = t.get('charging_amps',None)
            charger_power = t.get('charger_power',None)
            charge_limit = t.get('charge_limit',None)
            session_energy = t.get('session_energy', None)
            asleep_sensor = t.get('asleep_sensor', None)
            online_sensor = t.get('online_sensor',None)
            battery_sensor = t.get('battery_sensor',None)
            location_tracker = t.get('location_tracker',None)
            destination_location_tracker = t.get('destination_location_tracker',None)
            arrival_time = t.get('arrival_time',None)
            software_update = t.get('software_update',None)
            force_data_update = t.get('force_data_update', None)
            polling_switch = t.get('polling_switch',None)
            data_last_update_time = t.get('data_last_update_time',None)

            # Find sensors not provided:
            if 'charger' in t:
                car = t['charger']
            elif 'car' in t:
                car = t['car']
            if 'charger_sensor' in t:
                charger_sensor:str = t['charger_sensor']
                name = charger_sensor.replace(charger_sensor,'binary_sensor.','')
                name = name.replace(name,'_charger','')
                car = name

            sensor_states = self.ADapi.get_state(namespace = namespace)
            for sensor_id, sensor_states in sensor_states.items():

                if 'binary_sensor.' + car + '_charger' in sensor_id:
                    if not charger_sensor:
                        charger_sensor = sensor_id
                if 'switch.' + car + '_charger' in sensor_id:
                    if not charger_switch:
                        charger_switch = sensor_id
                if 'number.' + car + '_charging_amps' in sensor_id:
                    if not charging_amps:
                        charging_amps = sensor_id
                if 'sensor.' + car + '_charger_power' in sensor_id:
                    if not charger_power:
                        charger_power = sensor_id
                if 'number.' + car + '_charge_limit' in sensor_id:
                    if not charge_limit:
                        charge_limit = sensor_id
                if 'sensor.' + car + '_energy_added' in sensor_id:
                    if not session_energy:
                        session_energy = sensor_id
                if 'binary_sensor.' + car + '_asleep' in sensor_id:
                    if not asleep_sensor:
                        asleep_sensor = sensor_id
                if 'binary_sensor.' + car + '_online' in sensor_id:
                    if not online_sensor:
                        online_sensor = sensor_id
                if 'sensor.' + car + '_battery' in sensor_id:
                    if not battery_sensor:
                        battery_sensor = sensor_id
                if 'device_tracker.' + car + '_location_tracker' in sensor_id:
                    if not location_tracker:
                        location_tracker = sensor_id
                if 'device_tracker.' + car + '_destination_location_tracker' in sensor_id:
                    if not destination_location_tracker:
                        destination_location_tracker = sensor_id
                if 'sensor.' + car + '_arrival_time' in sensor_id:
                    if not arrival_time:
                        arrival_time = sensor_id
                if 'update.' + car + '_software_update' in sensor_id:
                    if not software_update:
                        software_update = sensor_id
                if 'button.' + car + '_force_data_update' in sensor_id:
                    if not force_data_update:
                        force_data_update = sensor_id
                if 'switch.' + car + '_polling' in sensor_id:
                    if not polling_switch:
                        polling_switch = sensor_id
                if 'sensor.' + car + '_data_last_update_time' in sensor_id:
                    if not data_last_update_time:
                        data_last_update_time = sensor_id


            teslaCar = Tesla_car(api = self.ADapi,
                namespace = namespace,
                carName = car,
                charger_sensor = charger_sensor,
                charge_limit = charge_limit,
                battery_sensor = battery_sensor,
                asleep_sensor = asleep_sensor,
                online_sensor = online_sensor,
                location_tracker = location_tracker,
                destination_location_tracker = destination_location_tracker,
                arrival_time = arrival_time,
                software_update = software_update,
                force_data_update = force_data_update,
                polling_switch = polling_switch,
                data_last_update_time = data_last_update_time,
                battery_size = t.get('battery_size',100),
                pref_charge_limit = t.get('pref_charge_limit',90),
                priority = t.get('priority',3),
                finishByHour = t.get('finishByHour',7),
                charge_now = t.get('charge_now',False),
                charge_only_on_solar = t.get('charge_only_on_solar',False),
                departure = t.get('departure',None)
            )
            self.cars.append(teslaCar)

            teslaCharger = Tesla_charger(api = self.ADapi,
                Car = teslaCar,
                namespace = namespace,
                charger = car,
                charger_sensor = charger_sensor,
                charger_switch = charger_switch,
                charging_amps = charging_amps,
                charger_power = charger_power,
                session_energy = session_energy
            )
            self.chargers.append(teslaCharger)


        # Setting up other cars
        other_cars = self.args.get('cars', [])
        for car in other_cars:
            automobile = Car(api = self.ADapi,
                namespace = car.get('namespace', namespace),
                carName = car.get('carName',None),
                charger_sensor = car.get('charger_sensor',None),
                charge_limit = car.get('charge_limit',None),
                battery_sensor = car.get('battery_sensor',None),
                asleep_sensor = car.get('asleep_sensor',None),
                online_sensor = car.get('online_sensor',None),
                location_tracker = car.get('location_tracker',None),
                software_update = car.get('software_update',None),
                force_data_update = car.get('force_data_update',None),
                polling_switch = car.get('polling_switch',None),
                data_last_update_time = car.get('data_last_update_time',None),
                battery_size = car.get('battery_size',100),
                pref_charge_limit = car.get('pref_charge_limit',100),
                priority = car.get('priority',3),
                finishByHour = car.get('finishByHour',7),
                charge_now = car.get('charge_now',False),
                charge_only_on_solar = car.get('charge_only_on_solar',False),
                departure = car.get('departure',None)
            )
            self.cars.append(automobile)


        # Setting up Easee charger
        easees = self.args.get('easee', [])
        for e in easees:
            namespace = e.get('namespace',self.HASS_namespace)
            charger_status = e.get('charger_status',None)
            reason_for_no_current = e.get('reason_for_no_current',None)
            current = e.get('current',None)
            charger_power = e.get('charger_power',None)
            voltage = e.get('voltage',None)
            max_charger_limit = e.get('max_charger_limit',None)
            idle_current = e.get('idle_current',False)
            online_sensor = e.get('online_sensor',None)
            session_energy = e.get('session_energy',None)

            # Find sensors not provided:
            if 'charger' in e:
                charger = e['charger']
            if 'charger_status' in e:
                charger_status:str = e['charger_status']
                name = charger_status.replace(charger_status,'sensor.','')
                name = name.replace(name,'_status','')
                charger = name

            sensor_states = self.ADapi.get_state(namespace = namespace)
            for sensor_id, sensor_states in sensor_states.items():
                if 'sensor.' + charger + '_status' in sensor_id:
                    if not charger_status:
                        charger_status = sensor_id
                if 'sensor.' + charger + '_reason_for_no_current' in sensor_id:
                    if not reason_for_no_current:
                        reason_for_no_current = sensor_id
                if 'sensor.' + charger + '_current' in sensor_id:
                    if not current:
                        current = sensor_id
                if 'sensor.' + charger + '_power' in sensor_id:
                    if not charger_power:
                        charger_power = sensor_id
                if 'sensor.' + charger + '_voltage' in sensor_id:
                    if not voltage:
                        voltage = sensor_id
                if 'sensor.' + charger + '_max_charger_limit' in sensor_id:
                    if not max_charger_limit:
                        max_charger_limit = sensor_id
                if 'switch.' + charger + 'idle_current' in sensor_id:
                    if not idle_current:
                        idle_current = sensor_id
                if 'binary_sensor.' + charger + '_online' in sensor_id:
                    if not online_sensor:
                        online_sensor = sensor_id
                if 'sensor.' + charger + '_session_energy' in sensor_id:
                    if not session_energy:
                        session_energy = sensor_id

            easeeCharger = Easee(api = self.ADapi,
                cars = self.cars,
                namespace = namespace,
                charger = charger,
                charger_sensor = charger_status,
                reason_for_no_current = reason_for_no_current,
                charging_amps = current,
                charger_power = charger_power,
                session_energy = session_energy,
                voltage = voltage,
                max_charger_limit = max_charger_limit,
                idle_current = idle_current,
                guest = e.get('guest', False)
            )
            self.chargers.append(easeeCharger)


            # Set up hot water boilers and electrical heaters
        self.heatersRedusedConsumption:list = [] # Heaters currently turned off/down due to overconsumption
        self.lastTimeHeaterWasReduced = datetime.datetime.now() - datetime.timedelta(minutes = 5)

        heaters = self.args.get('climate', {})
        for heater in heaters:
            validConsumptionSensor: bool = True
            namespace = heater.get('namespace',self.HASS_namespace)
            if 'name' in heater:
                log_indoor_sens:bool = True
                sensor_states = self.ADapi.get_state(namespace = namespace)
                for sensor_id, sensor_states in sensor_states.items():
                    if 'climate.' + heater['name'] in sensor_id:
                        if not 'heater' in heater:
                            heater['heater'] = sensor_id
                    if (
                        'sensor.' + heater['name'] + '_electric_consumption_w' in sensor_id
                        or 'sensor.' + heater['name'] + '_electric_consumed_w' in sensor_id
                    ):
                        if not 'consumptionSensor' in heater:
                            heater['consumptionSensor'] = sensor_id
                    if (
                        'sensor.' + heater['name'] + '_electric_consumption_kwh' in sensor_id
                        or 'sensor.' + heater['name'] + '_electric_consumed_kwh' in sensor_id
                    ):
                        if not 'kWhconsumptionSensor' in heater:
                            heater['kWhconsumptionSensor'] = sensor_id
                    if 'sensor.' + heater['name'] + '_air_temperature' in sensor_id:
                        if (
                            not 'indoor_sensor_temp' in heater
                            and log_indoor_sens
                        ):
                            indoor_sensor_temp_found = sensor_id
                            self.ADapi.log(
                                f"No external indoor temperature sensor for {heater['name']} is configured. "
                                f"Automation will not check if it is hot inside. Found sensor {indoor_sensor_temp_found}. "
                                "This can be configured with 'indoor_sensor_temp' if applicable.",
                                level = 'INFO'
                            )
                            log_indoor_sens = False

            if not 'heater' in heater:
                self.ADapi.log(
                    f"'heater' not found or configured in {heater} climate configuration. Climate control setup aborted",
                    level = 'WARNING'
                )
                continue

            if not 'consumptionSensor' in heater:
                validConsumptionSensor = False
                heatername = (str(heater['heater'])).split('.')
                heater['consumptionSensor'] = 'input_number.' + heatername[1] + '_power'
                if not self.ADapi.entity_exists(heater['consumptionSensor'], namespace = namespace):
                    powercapability = heater.get('power', 300)
                    self.ADapi.call_service("state/set",
                        entity_id = heater['consumptionSensor'],
                        attributes = {'friendly_name' : str(heatername[1]) + ' Power'},
                        state = powercapability,
                        namespace = namespace
                    )

                self.ADapi.log(
                    f"'consumptionSensor' not found or configured. Climate electricity control not optimal. "
                    f"Using {heater['consumptionSensor']} as state with power: "
                    f"{self.ADapi.get_state(heater['consumptionSensor'], namespace = namespace)}",
                    level = 'WARNING'
                )
                if not 'power' in heater:
                    self.ADapi.log(f"Set electrical consumption with 'power' in args for heater.", level = 'INFO')

            if not 'kWhconsumptionSensor' in heater:
                heater['kWhconsumptionSensor'] = 'input_number.zero'
                if not self.ADapi.entity_exists(heater['kWhconsumptionSensor'], namespace = namespace):
                    self.ADapi.call_service("state/set",
                        entity_id = heater['kWhconsumptionSensor'],
                        attributes = {'friendly_name' : 'Zero consumption helper'},
                        state = 0,
                        namespace = namespace
                    )

                self.ADapi.log(
                    "'kWhconsumptionSensor' not found or configured. Climate electricity logging not available. "
                    "Using input_number.zero as state",
                    level = 'WARNING'
                )


            climate = Climate(api = self.ADapi,
                heater = heater['heater'],
                consumptionSensor = heater['consumptionSensor'],
                validConsumptionSensor = validConsumptionSensor,
                kWhconsumptionSensor = heater['kWhconsumptionSensor'],
                max_continuous_hours = heater.get('max_continuous_hours', 2),
                on_for_minimum = heater.get('on_for_minimum', 6),
                pricedrop = heater.get('pricedrop', 1),
                pricedifference_increase = heater.get('pricedifference_increase', 1.07),
                namespace = heater.get('namespace', self.HASS_namespace),
                away = heater.get('vacation', self.away_state),
                automate = heater.get('automate', self.automate),
                recipient = heater.get('recipient', None),
                indoor_sensor_temp = heater.get('indoor_sensor_temp', None),
                window_temp = heater.get('window_temp', None),
                window_offset = heater.get('window_offset', -3),
                target_indoor_input = heater.get('target_indoor_input', None),
                target_indoor_temp = heater.get('target_indoor_temp', 23),
                save_temp_offset = heater.get('save_temp_offset', None),
                save_temp = heater.get('save_temp', None),
                away_temp = heater.get('away_temp', None),
                rain_level = heater.get('rain_level', self.rain_level),
                anemometer_speed = heater.get('anemometer_speed', self.anemometer_speed),
                low_price_max_continuous_hours = heater.get('low_price_max_continuous_hours', 2),
                priceincrease = heater.get('priceincrease', 1),
                windowsensors = heater.get('windowsensors', []),
                getting_cold = heater.get('getting_cold', 18),
                daytime_savings = heater.get('daytime_savings', []),
                temperatures = heater.get('temperatures', [])
            )
            self.heaters.append(climate)


        heater_switches = self.args.get('heater_switches', {})
        for heater_switch in heater_switches:
            validConsumptionSensor:bool = True
            namespace = heater_switch.get('namespace',self.HASS_namespace)
            if 'name' in heater_switch:
                sensor_states = self.ADapi.get_state(namespace = namespace)
                for sensor_id, sensor_states in sensor_states.items():
                    if 'switch.' + heater_switch['name'] in sensor_id:
                        if not 'switch' in heater_switch:
                            heater_switch['switch'] = sensor_id
                    if (
                        'sensor.' + heater_switch['name'] + '_electric_consumption_w' in sensor_id
                        or 'sensor.' + heater_switch['name'] + '_electric_consumed_w' in sensor_id
                    ):
                        if not 'consumptionSensor' in heater_switch:
                            heater_switch['consumptionSensor'] = sensor_id
                    if (
                        'sensor.' + heater_switch['name'] + '_electric_consumption_kwh' in sensor_id
                        or 'sensor.' + heater_switch['name'] + '_electric_consumed_kwh' in sensor_id
                    ):
                        if not 'kWhconsumptionSensor' in heater_switch:
                            heater_switch['kWhconsumptionSensor'] = sensor_id

            if not 'switch' in heater_switch:
                self.ADapi.log(
                    "'switch' not found or configured in on_off_switch configuration. "
                    "on_off_switch control setup aborted",
                    level = 'WARNING'
                )
                continue

            if not 'consumptionSensor' in heater_switch:
                validConsumptionSensor = False
                heatername = (str(heater_switch['switch'])).split('.')
                heater_switch['consumptionSensor'] = 'input_number.' + heatername[1] + '_power'
                if not self.ADapi.entity_exists(heater_switch['consumptionSensor'], namespace = namespace):
                    powercapability = heater_switch.get('power', 1000)
                    self.ADapi.call_service("state/set",
                        entity_id = heater['consumptionSensor'],
                        attributes = {'friendly_name' : str(heatername[1]) + ' Power'},
                        state = powercapability,
                        namespace = namespace
                    )

                self.ADapi.log(
                    f"'consumptionSensor' not found or configured. on_off_switch electricity control not optimal. "
                    f"Using {heater_switch['consumptionSensor']} as state with power: "
                    f"{self.ADapi.get_state(heater_switch['consumptionSensor'], namespace = namespace)}",
                    level = 'WARNING'
                )
                if not 'power' in heater_switch:
                    self.ADapi.log(f"Set electrical consumption with 'power' in args for on_off_switch.", level = 'INFO')

            if not 'kWhconsumptionSensor' in heater_switch:
                heater_switch['kWhconsumptionSensor'] = 'input_number.zero'
                if not self.ADapi.entity_exists(heater_switch['kWhconsumptionSensor'], namespace = namespace):
                    self.ADapi.call_service("state/set",
                        entity_id = heater['kWhconsumptionSensor'],
                        attributes = {'friendly_name' : 'Zero consumption helper'},
                        state = 0,
                        namespace = namespace
                    )
                self.ADapi.log(
                    "'kWhconsumptionSensor' not found or configured. on_off_switch electricity logging not available. "
                    "Using input_number.zero as state",
                    level = 'WARNING'
                )

            on_off_switch = On_off_switch(api = self.ADapi,
                heater = heater_switch['switch'],
                consumptionSensor = heater_switch['consumptionSensor'],
                validConsumptionSensor = validConsumptionSensor,
                kWhconsumptionSensor = heater_switch['kWhconsumptionSensor'],
                max_continuous_hours = heater_switch.get('max_continuous_hours',8),
                on_for_minimum = heater_switch.get('on_for_minimum',6),
                pricedrop = heater_switch.get('pricedrop',0.3),
                pricedifference_increase = heater_switch.get('pricedifference_increase', 1.07),
                namespace = heater_switch.get('namespace', self.HASS_namespace),
                away = heater_switch.get('vacation',self.away_state),
                automate = heater_switch.get('automate', self.automate),
                recipient = heater_switch.get('recipient', None)
            )
            self.heaters.append(on_off_switch)


            # Set up appliances with remote start function to run when electricity price is at its lowest
            """ TODO:
                Move to another app
                Electrical appliances like washing mashimes should only be used when awake. Use at own risk.
            """
        appliances = self.args.get('appliances', [])
        for appliance in appliances:
            namespace = appliance.get('namespace',self.HASS_namespace)
            if 'remote_start' in appliance:
                remote_start = appliance['remote_start']
                if 'night' in appliance:
                    nightprogram = appliance['night']
                else:
                    nightprogram = None
                    self.ADapi.log(
                        f"Night program not configured for {self.ADapi.get_state(remote_start, attribute='friendly_name')}.",
                        level = 'INFO'
                    )
                if 'day' in appliance:
                    dayprogram = appliance['day']
                else:
                    dayprogram = None
                    self.ADapi.log(
                        f"Day program not configured for {self.ADapi.get_state(remote_start, attribute='friendly_name')}.",
                        level = 'INFO'
                    )

                machine = Appliances(api = self.ADapi,
                    remote_start = remote_start,
                    nightprogram = nightprogram,
                    dayprogram = dayprogram,
                    namespace = appliance.get('namespace', self.HASS_namespace),
                    away = appliance.get('vacation', self.away_state)
                )
                self.appliances.append(machine)


        # Variables for different calculations 
        self.accumulated_unavailable:int = 0
        self.last_accumulated_kWh:float = 0
        self.accumulated_kWh_wasUnavailable:bool = False
        self.SolarProducing_ChangeToZero:bool = False
        self.notify_about_overconsumption:bool = False
        self.totalWattAllHeaters:float = 0

        self.houseIsOnFire:bool = False

        self.checkIdleConsumption_Handler = None

        runtime = datetime.datetime.now()
        addseconds = (round((runtime.minute*60 + runtime.second)/60)+1)*60
        runtime = runtime.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(seconds=addseconds)

        self.ADapi.run_every(self.checkElectricalUsage, runtime, 60)
        self.ADapi.listen_state(self.electricityprices_updated, ELECTRICITYPRICE.nordpool_prices,
            attribute = 'tomorrow',
            duration = 120
        )
        self.ADapi.listen_event(self.mode_event, "MODE_CHANGE",
            namespace = self.HASS_namespace
        )
        self.ADapi.run_in(self.calculateIdleConsumption, 70)


    def terminate(self) -> None:
        """ Writes charger and car data to persisten storage before terminating app.
        """
        try:
            with open(self.json_path, 'r') as json_read:
                ElectricityData = json.load(json_read)
            for charger in self.chargers:
                if charger.charger_id in ElectricityData['charger']:
                    car_connected = None
                    if charger.Car is not None:
                        car_connected = charger.Car.carName

                    ElectricityData['charger'][charger.charger_id].update({
                        "voltPhase" : charger.voltPhase,
                        "MaxAmp" : charger.maxChargerAmpere,
                        "ConnectedCar" : car_connected
                    })

            for car in self.cars:
                if car.vehicle_id in ElectricityData['car']:

                    ElectricityData['car'][car.vehicle_id].update({
                        "CarLimitAmpere" : car.car_limit_max_charging,
                        "MaxkWhCharged" : car.maxkWhCharged,
                        "batterysize" : car.battery_size,
                        "Counter" : car.battery_reg_counter
                    })

            completeQueue:list = []
            if CHARGE_SCHEDULER.chargingQueue:
                try:
                    for q in CHARGE_SCHEDULER.chargingQueue:
                        queue:dict = {}
                        for key, value in q.items():
                            if isinstance(value, datetime.datetime):
                                queue.update({key: value.isoformat()})
                            else:
                                queue.update({key : value})
                        completeQueue.append(queue)
                except Exception:
                    self.ADapi.log(f"Was not able to stor chargingQueue when terminating.", level = 'INFO')
                    completeQueue:list = []

            for heater in self.heaters:
                if heater.heater in ElectricityData['consumption']:
                    ElectricityData['consumption'][heater.heater].update({'peak_hours' : heater.peak_hours})

            ElectricityData['chargingQueue'] = completeQueue
            ElectricityData['queueChargingList'] = self.queueChargingList
            ElectricityData['solarChargingList'] = self.solarChargingList

            with open(self.json_path, 'w') as json_write:
                json.dump(ElectricityData, json_write, indent = 4)

        except FileNotFoundError:
            self.ADapi.log(f"FileNotFound when ElectricityManagement Terminated", level = 'INFO')


    def electricityprices_updated(self, entity, attribute, old, new, kwargs) -> None:
        """ Updates times to save/spend and charge with new prices available.
        """
        for heater in self.heaters:
            self.ADapi.run_in(heater.heater_getNewPrices, delay = 0, random_start = 1, random_end = 2)

        if len(new) > 0:
            self.ADapi.run_in(self.calculateIdleConsumption, 20)
            self.ADapi.run_in(self.findConsumptionAfterTurnedBackOn, 30)

            for c in self.cars:
                if c.getLocation() == 'home':
                    self.ADapi.run_in(c.findNewChargeTimeAt, 300)
            
            if self.checkIdleConsumption_Handler != None:
                if self.ADapi.timer_running(self.checkIdleConsumption_Handler):
                    try:
                        self.ADapi.cancel_timer(self.checkIdleConsumption_Handler)
                    except Exception as e:
                        self.ADapi.log(
                            f"Was not able to stop existing handler to log consumption. {e}",
                            level = "DEBUG"
                        )
            if self.ADapi.get_state(self.away_state) == 'off':
                self.checkIdleConsumption_Handler = self.ADapi.run_at(self.logIdleConsumption, '04:30:00')


    def checkElectricalUsage(self, kwargs) -> None:
        """ Calculate and ajust consumption to stay within kWh limit.
            Start charging when time to charge.
        """
        accumulated_kWh = self.ADapi.get_state(self.accumulated_consumption_current_hour)
        current_consumption = self.ADapi.get_state(self.current_consumption)

        runtime = datetime.datetime.now()
        remaining_minute:int = 60 - int(runtime.minute)

            # Check if consumption sensors is valid
        if (
            current_consumption == 'unavailable'
            or current_consumption == 'unknown'
        ):
            current_consumption:float = 0.0
            with open(JSON_PATH, 'r') as json_read:
                ElectricityData = json.load(json_read)

            if ElectricityData['consumption']['idleConsumption']['ConsumptionData']:
                out_temp_str = str(math.floor(OUT_TEMP / 2.) * 2)
                multiply_with_to_be_safe:float = 2
                    # Find closest temp registered with data
                try:
                    closest_temp = ElectricityData['consumption']['idleConsumption']['ConsumptionData'][out_temp_str]
                except Exception:
                    temp_diff:int = 100
                    closest_temp:int
                    for temps in ElectricityData['consumption']['idleConsumption']['ConsumptionData']:
                        if OUT_TEMP > float(temps):
                            if temp_diff < OUT_TEMP - float(temps):
                                continue
                            temp_diff = OUT_TEMP - float(temps)
                            closest_temp = temps
                        else:
                            if temp_diff < float(temps) - OUT_TEMP:
                                continue
                            temp_diff = float(temps) - OUT_TEMP
                            closest_temp = temps

                    out_temp_str = str(closest_temp)

                current_consumption = (
                    float(ElectricityData['consumption']['idleConsumption']['ConsumptionData'][out_temp_str]['Consumption'])
                    * multiply_with_to_be_safe
                )

            for heater in self.heaters:
                if heater.validConsumptionSensor:
                    try:
                        current_consumption += float(self.ADapi.get_state(heater.consumptionSensor,
                            namespace = heater.namespace))
                    except Exception:
                        pass
            for c in self.cars:
                if (
                    c.getLocation() == 'home'
                    and c.getCarChargerState() == 'Charging'
                    and c.connectedCharger is not None
                ):
                    try:
                        current_consumption += c.connectedCharger.ampereCharging * c.connectedCharger.voltPhase
                    except Exception:
                        self.ADapi.log(
                            f"Not able to get charging info when current consumption is unavailable from {type(c.connectedCharger).__name__}",
                            level = 'WARNING'
                        )          

        else:
            current_consumption = float(current_consumption)

        try:
            accumulated_kWh = float(accumulated_kWh)
        except Exception as e:
            if self.accumulated_unavailable > 5:
                # Will try to reload Home Assistant integration every sixth minute the sensor is unavailable. 
                self.accumulated_unavailable = 0
                self.ADapi.call_service('homeassistant/reload_config_entry',
                    entity_id = self.accumulated_consumption_current_hour
                )
            else:
                self.accumulated_unavailable += 1

            try:
                accumulated_kWh = self.last_accumulated_kWh
            except Exception as e:
                accumulated_kWh = round(float(runtime.minute/60) * (self.max_kwh_usage_pr_hour - self.buffer),2)
                self.ADapi.log(f"Failed to get last accumulated kwh. Exception: {e}", level = 'WARNING')

            accumulated_kWh = round(self.last_accumulated_kWh + (current_consumption/60000),2)
            self.last_accumulated_kWh = accumulated_kWh
            self.accumulated_kWh_wasUnavailable = True

        else:
            if self.accumulated_kWh_wasUnavailable:
                # Log estimated during unavailable vs actual
                self.accumulated_kWh_wasUnavailable = False
                if self.last_accumulated_kWh + (current_consumption/60000) < accumulated_kWh:
                    self.ADapi.log(
                        f"Accumulated kWh was unavailable. Estimated: {round(self.last_accumulated_kWh + (current_consumption/60000),2)}. "
                        f"Actual: {accumulated_kWh}",
                        level = 'INFO'
                    )

            self.last_accumulated_kWh = accumulated_kWh
            attr_last_updated = self.ADapi.get_state(entity_id = self.accumulated_consumption_current_hour,
                attribute = "last_updated"
            )
            if not attr_last_updated:
                last_update: datetime = self.ADapi.datetime(aware=True)
            else:
                last_update = self.ADapi.convert_utc(attr_last_updated)

            now: datetime = self.ADapi.datetime(aware=True)
            stale_time = now - last_update
            if stale_time > datetime.timedelta(minutes = 2): # Stale for more than two minutes. Reload integration
                self.ADapi.call_service('homeassistant/reload_config_entry',
                    entity_id = self.accumulated_consumption_current_hour
                )

                if runtime.minute < 2:
                    accumulated_kWh = 1
                    self.last_accumulated_kWh = 1
                else:
                    accumulated_kWh += 0.5
                    self.last_accumulated_kWh += 0.5
                return


            # Check if production sensors exists and valid
        if self.current_production:
            current_production = self.ADapi.get_state(self.current_production)
            if (
                current_production == 'unavailable'
                or current_production == 'unknown'
            ):
                current_production = 0
        else:
            current_production = 0
        if self.accumulated_production_current_hour:
            production_kWh = self.ADapi.get_state(self.accumulated_production_current_hour)
            if (
                production_kWh == 'unavailable'
                or production_kWh == 'unknown'
            ):
                production_kWh = 0
        else:
            production_kWh = 0


            # Calculations used to adjust consumption
        max_target_kWh_buffer:float = round(
            ((self.max_kwh_usage_pr_hour
            - self.buffer) * (runtime.minute/60))
            - (accumulated_kWh - production_kWh),
        2)
        projected_kWh_usage:float = round(
            ((current_consumption - current_production) /60000)
            * remaining_minute,
        2)
        available_Wh:float = round(
            (self.max_kwh_usage_pr_hour
            - self.buffer
            + (max_target_kWh_buffer * (60 / remaining_minute)))*1000
            - current_consumption,
        2)


        if runtime.minute == 0:
            # Resets and logs every hour
            self.last_accumulated_kWh = 0
            if (
                datetime.datetime.now().hour == 0
                and datetime.datetime.now().day == 1
            ):
                self.resetHighUsage()

            elif accumulated_kWh > self.top_usage_hour:
                self.logHighUsage()

            for c in self.cars:
                if (
                    c.getLocation() == 'home'
                    and c.getCarChargerState() == 'Charging'
                ):
                    if (
                        (c.priority == 1 or c.priority == 2)
                        and CHARGE_SCHEDULER.isPastChargingTime(vehicle_id = c.vehicle_id)
                    ):
                        pass

                    elif (
                        not c.dontStopMeNow()
                        and not self.SolarProducing_ChangeToZero
                    ):
                        if not CHARGE_SCHEDULER.isChargingTime(vehicle_id = c.vehicle_id):
                            c.stopCharging()
                            if CHARGE_SCHEDULER.isPastChargingTime(vehicle_id = c.vehicle_id):
                                self.ADapi.log(
                                    f"Was not able to finish charging {c.carName} "
                                    f"with {c.kWhRemaining()} kWh remaining before prices increased.",
                                    level = 'INFO'
                                )
                            elif runtime.hour != 0:
                                c.kWhRemaining()
                                c.findNewChargeTime()

            for heater in reversed(self.heatersRedusedConsumption):
                heater.isOverconsumption = False
                self.heatersRedusedConsumption.remove(heater)


            """ Change consumption if above target or below production: """

        elif (
            projected_kWh_usage + accumulated_kWh > self.max_kwh_usage_pr_hour - self.buffer
            or max_target_kWh_buffer < 0
        ):
            # Current consuption is on it´s way to go over max kWh usage pr hour. Redusing usage

            if (
                available_Wh > -800
                and remaining_minute > 15
                and not self.heatersRedusedConsumption
            ):
                return

            if available_Wh < -2000:
                self.findCharingNotInQueue()

            if self.queueChargingList:
                reduce_Wh, available_Wh = self.getHeatersReducedPreviousConsumption(available_Wh)

                if  reduce_Wh + available_Wh < 0 :
                    available_Wh = self.reduceChargingAmpere(available_Wh, reduce_Wh)

            if (
                runtime.minute > 7
                or not self.queueChargingList
            ):
                for heater in self.heaters:
                    if available_Wh < -100:
                        try:
                            heater.prev_consumption = float(self.ADapi.get_state(heater.consumptionSensor,
                                namespace = heater.namespace)
                            )
                        except ValueError:
                            pass
                        else:
                            if (
                                heater.prev_consumption > 100
                                and heater not in self.heatersRedusedConsumption
                            ):
                                self.heatersRedusedConsumption.append(heater)
                                heater.setSaveState()
                                if (
                                    self.ADapi.get_state(heater.heater,
                                        attribute = 'hvac_action',
                                        namespace = heater.namespace
                                    ) == 'heating'
                                    or heater.validConsumptionSensor
                                ):
                                    available_Wh += heater.prev_consumption
                    else:
                        return

            if (
                available_Wh < -100
                and datetime.datetime.now() - self.lastTimeHeaterWasReduced > datetime.timedelta(minutes = 3)
                and remaining_minute <= 40
            ):
                if self.pause_charging:
                    for queue_id in  reversed(self.queueChargingList):
                        for c in self.chargers:
                            if c.Car is not None:
                                if c.Car.vehicle_id == queue_id:
                                    if c.getChargingState() == 'Charging':
                                        available_Wh += c.ampereCharging * c.voltPhase
                                        c.stopCharging()
                                        if available_Wh > -100:
                                            return
                                    break

                if self.notify_overconsumption:
                    if self.notify_about_overconsumption:
                        self.notify_about_overconsumption = False
                        data = {
                            'tag' : 'overconsumption'
                            }

                        NOTIFY_APP.send_notification(
                            message = f"Turn down consumption. Is's about to go over max usage with {round(-available_Wh,0)} "
                            "remaining to reduce",
                            message_title = "⚡High electricity usage",
                            message_recipient = RECIPIENTS,
                            also_if_not_home = False,
                            data = data
                        )
                    else:
                        self.notify_about_overconsumption = True


        elif self.heatersRedusedConsumption:
            # Reduce charging speed to turn heaters back on
            self.findCharingNotInQueue()
            self.notify_about_overconsumption = False

            reduce_Wh, available_Wh = self.getHeatersReducedPreviousConsumption(available_Wh)
 
            if (
                self.queueChargingList
                and reduce_Wh + available_Wh < 0
            ):
                available_Wh = self.reduceChargingAmpere(available_Wh, reduce_Wh)


        elif (
            accumulated_kWh <= production_kWh
            and projected_kWh_usage < 0
        ):
            # Production is higher than consumption
            # TODO: Not tested properly

            self.notify_about_overconsumption = False
            self.SolarProducing_ChangeToZero = True
            overproduction_Wh:float = round(current_production - current_consumption , 2)

            # Check if any heater is reduced
            if self.heatersRedusedConsumption:
                for heater in reversed(self.heatersRedusedConsumption):
                    if heater.prev_consumption < overproduction_Wh:
                        heater.setPreviousState()
                        overproduction_Wh -= heater.prev_consumption
                        self.heatersRedusedConsumption.remove(heater)


            # TODO: If chargetime: Calculate if production is enough to charge wanted amount

            if not self.solarChargingList :
                # Check if any is charging, or is not finished
                for c in self.cars:
                    if (
                        c.getLocation() == 'home'
                        and c.connectedCharger is not None
                    ):
                        if c.getCarChargerState() == 'Charging':
                            c.charging_on_solar = True
                            self.solarChargingList.append(c.vehicle_id)
                        elif (
                            (c.getCarChargerState() == 'Stopped'
                            or c.getCarChargerState() == 'awaiting_start')
                            and c.car_battery_soc() < c.pref_charge_limit
                            and overproduction_Wh > 1600
                        ):
                            c.startCharging()
                            c.charging_on_solar = True
                            self.solarChargingList.append(c.vehicle_id)
                            AmpereToCharge = math.ceil(overproduction_Wh / c.voltPhase)
                            c.connectedCharger.setChargingAmps(charging_amp_set = AmpereToCharge)
                            return
                    elif c.isConnected():
                        c.connectedCharger = c.onboardCharger

                # Check if any is below prefered charging limit
                for c in self.cars:
                    if (
                        c.getLocation() == 'home'
                        and c.connectedCharger is not None
                    ):
                        if c.getCarChargerState() == 'Charging':
                            self.solarChargingList.append(c.vehicle_id)
                            c.charging_on_solar = True
                        elif (
                            c.pref_charge_limit > c.oldChargeLimit
                        ):
                            c.charging_on_solar = True
                            c.changeChargeLimit(c.pref_charge_limit)
                            c.startCharging()
                            self.solarChargingList.append(c.vehicle_id)
                            AmpereToCharge = math.ceil(overproduction_Wh / c.voltPhase)
                            c.connectedCharger.setChargingAmps(charging_amp_set = AmpereToCharge)
                            return

            else :
                for queue_id in self.solarChargingList:
                    for c in self.cars:
                        if (
                            c.vehicle_id == queue_id
                            and c.connectedCharger is not None
                        ):
                            if c.getCarChargerState() == 'Charging':
                                AmpereToIncrease = math.ceil(overproduction_Wh / c.voltPhase)
                                c.connectedCharger.changeChargingAmps(charging_amp_change = AmpereToIncrease)
                                return
                            elif (
                                c.getCarChargerState() == 'Complete'
                                and c.car_battery_soc() >= c.pref_charge_limit
                            ):
                                c.charging_on_solar = False
                                c.changeChargeLimit(c.oldChargeLimit)
                                try:
                                    self.solarChargingList.remove(queue_id)
                                except Exception as e:
                                    self.ADapi.log(f"{c.carName} was not in solarChargingList. Exception: {e}", level = 'DEBUG')
                            elif c.getCarChargerState() == 'Complete':
                                c.charging_on_solar = False
                                try:
                                    self.solarChargingList.remove(queue_id)
                                except Exception as e:
                                    self.ADapi.log(f"{c.carName} was not in solarChargingList. Exception: {e}", level = 'DEBUG')
                return

            # Set spend in heaters
            for heater in self.heaters:
                if (
                    float(self.ADapi.get_state(heater.consumptionSensor, namespace = heater.namespace)) < 100
                    and not heater.increase_now
                    and heater.normal_power < overproduction_Wh
                ):
                    heater.setIncreaseState()
                    overproduction_Wh -= heater.normal_power

        elif (
            (accumulated_kWh > production_kWh
            or projected_kWh_usage > 0)
            and self.SolarProducing_ChangeToZero
        ):
            # Consumption is higher than production
            # TODO: Not tested properly

            self.notify_about_overconsumption = False
            overproduction_Wh:float = round(current_production - current_consumption , 2)

            # Remove spend in heaters
            for heater in self.heaters:
                if overproduction_Wh > 0:
                    return

                if heater.increase_now:
                    heater.setPreviousState()
                    overproduction_Wh += heater.normal_power

            # Reduce any chargers/batteries
            for queue_id in reversed(self.solarChargingList):
                for c in self.chargers:
                    if c.Car is not None:
                        if c.Car.vehicle_id == queue_id:

                            if c.ampereCharging == 0:
                                c.ampereCharging = math.floor(float(self.ADapi.get_state(c.charging_amps,
                                    namespace = c.namespace))
                                )

                            if c.ampereCharging > c.min_ampere:
                                AmpereToReduce = math.floor(overproduction_Wh / c.voltPhase)
                                if (c.ampereCharging + AmpereToReduce) < c.min_ampere:
                                    c.setChargingAmps(charging_amp_set = c.min_ampere)
                                    overproduction_Wh += (c.ampereCharging - c.min_ampere) * c.voltPhase
                                    # TODO: Check if remaining available is lower than production and stop charing.
                                else:
                                    c.changeChargingAmps(charging_amp_change = AmpereToReduce)
                                    overproduction_Wh += AmpereToReduce * c.voltPhase
                                    break

            if current_production < 1000:
                # TODO: Find proper idle consumption... 
                # If production is low -> stop and reset.

                self.SolarProducing_ChangeToZero = False
                for queue_id in reversed(self.solarChargingList):
                    for c in self.cars:
                        if c.vehicle_id == queue_id:
                            c.charging_on_solar = False
                            c.changeChargeLimit(c.oldChargeLimit)
                            try:
                                self.solarChargingList.remove(queue_id)
                            except Exception as e:
                                self.ADapi.log(f"{c.carName} was not in solarChargingList. Exception: {e}", level = 'DEBUG')


        elif (
            projected_kWh_usage + accumulated_kWh < self.max_kwh_usage_pr_hour - self.buffer
            and max_target_kWh_buffer > 0
            and not self.houseIsOnFire
        ):
            # Increase charging speed or add another charger if time to charge
            self.notify_about_overconsumption = False

            if (
                (remaining_minute > 9 and available_Wh < 800)
                or max_target_kWh_buffer < 0.1
                or datetime.datetime.now() - self.lastTimeHeaterWasReduced < datetime.timedelta(minutes = 4)
            ):
                return

            if self.findCharingNotInQueue():
                vehicle_id = None
                
                if self.queueChargingList:

                    for queue_id in self.queueChargingList:
                        for c in self.cars:
                            if (
                                c.vehicle_id == queue_id
                                and c.connectedCharger is not None
                            ):
                                ChargingState = c.getCarChargerState()
                                if (
                                    ChargingState == 'Complete'
                                    or ChargingState == 'Disconnected'
                                ):
                                    try:
                                        self.queueChargingList.remove(queue_id)
                                        if (
                                            not self.queueChargingList
                                            and self.ADapi.now_is_between('23:00:00', '06:00:00')
                                            and self.ADapi.get_state(self.away_state) == 'off'
                                        ):
                                            if CHARGE_SCHEDULER.findNextChargerToStart() == None:
                                                self.ADapi.run_in(self.logIdleConsumption, 30)
                                    except Exception as e:
                                        self.ADapi.log(f"{c.carName} was not in queueChargingList. Exception: {e}", level = 'DEBUG')

                                elif (
                                    ChargingState == 'Stopped'
                                    or ChargingState == 'awaiting_start'   
                                ):
                                    if not CHARGE_SCHEDULER.isChargingTime(vehicle_id = c.vehicle_id):
                                        try:
                                            self.queueChargingList.remove(queue_id)
                                        except Exception as e:
                                            self.ADapi.log(
                                                f"Was not able to remove {c.carName} from queueChargingList. Exception: {e}",
                                                level = 'DEBUG'
                                            )
                                    elif runtime.minute > 3:
                                        c.startCharging()
                                        AmpereToCharge = math.floor(available_Wh / c.connectedCharger.voltPhase)
                                        c.connectedCharger.setChargingAmps(charging_amp_set = AmpereToCharge)
                                        return

                                elif ChargingState == 'Charging':
                                    if not c.connectedCharger.isChargingAtMaxAmps():
                                        AmpereToIncrease = math.floor(available_Wh / c.connectedCharger.voltPhase)
                                        c.connectedCharger.changeChargingAmps(charging_amp_change = AmpereToIncrease)
                                        return

                                    else:
                                        if (
                                            len(CHARGE_SCHEDULER.chargingQueue) > len(self.queueChargingList)
                                            and available_Wh > 1600
                                            and runtime.minute > 15
                                            and remaining_minute > 5
                                        ):
                                            vehicle_id = CHARGE_SCHEDULER.findChargerToStart()
                                            if (
                                                c.vehicle_id == vehicle_id
                                                and remaining_minute > 12
                                            ):
                                                vehicle_id = CHARGE_SCHEDULER.findNextChargerToStart()

                                elif ChargingState == None:
                                    c.wakeMeUp()
                                elif (
                                    c.connectedCharger is not c.onboardCharger
                                    and ChargingState == 'NoPower'
                                ):
                                    c.wakeMeUp()
                                else:
                                    if (
                                        c.connectedCharger is c.onboardCharger
                                        and ChargingState == 'NoPower'
                                    ):
                                        c.connectedCharger = None
                                        try:
                                            self.queueChargingList.remove(queue_id)
                                        except Exception as e:
                                            self.ADapi.log(
                                                f"Was not able to remove {c.carName} from queueChargingList. Exception: {e}",
                                                level = 'DEBUG'
                                            )
                                    else:
                                        try:
                                            self.queueChargingList.remove(queue_id)
                                        except Exception as e:
                                            self.ADapi.log(
                                                f"Was not able to remove {c.carName} from queueChargingList. Exception: {e}",
                                                level = 'DEBUG'
                                            )

                            elif c.vehicle_id == queue_id:
                                if not c.isConnected():
                                    try:
                                        self.queueChargingList.remove(queue_id)
                                    except Exception as e:
                                        self.ADapi.log(
                                            f"Was not able to remove {c.carName} from queueChargingList. Exception: {e}",
                                            level = 'DEBUG'
                                        )
                                    c.removeFromQueue()
                                else:
                                    c.connectedCharger = c.onboardCharger
                                    if c.connectedCharger.Car is None:
                                        c.connectedCharger.Car = c

                if not self.queueChargingList:

                    if (
                        CHARGE_SCHEDULER.isChargingTime()
                        and available_Wh > 1600
                        and remaining_minute > 9
                        and runtime.minute > 3
                    ):
                        if vehicle_id == None:
                            vehicle_id = CHARGE_SCHEDULER.findChargerToStart()

                if vehicle_id != None:
                    if self.checkIdleConsumption_Handler != None:
                        if self.ADapi.timer_running(self.checkIdleConsumption_Handler):
                            try:
                                self.ADapi.cancel_timer(self.checkIdleConsumption_Handler)
                            except Exception as e:
                                self.ADapi.log(
                                    f"Was not able to stop existing handler to log consumption. {e}",
                                    level = "DEBUG"
                                )
                        self.checkIdleConsumption_Handler = None

                    for c in self.cars:
                        if (
                            c.vehicle_id == vehicle_id
                            and c.connectedCharger is not None
                        ):
                            if c.vehicle_id not in self.queueChargingList:
                                self.queueChargingList.append(c.vehicle_id)
                                c.startCharging()
                                AmpereToCharge = math.floor(available_Wh / c.connectedCharger.voltPhase)
                                c.connectedCharger.setChargingAmps(charging_amp_set = AmpereToCharge)
                                return
                        elif c.vehicle_id == vehicle_id:
                            if not c.isConnected():
                                c.removeFromQueue()
                            elif c.getCarChargerState() == 'NoPower':
                                for charger in self.chargers:
                                    if (
                                        charger.Car is None
                                        and charger.getChargingState() != 'Disconnected'
                                    ):
                                        charger.findCarConnectedToCharger()
                                        return

                            c.connectedCharger = c.onboardCharger
                            if c.connectedCharger.Car is None:
                                c.connectedCharger.Car = c


    def reduceChargingAmpere(self, available_Wh: float, reduce_Wh: float) -> float:
        """ Reduces charging to stay within max kWh.
        """
        reduce_Wh += available_Wh

        for queue_id in reversed(self.queueChargingList):
            for c in self.chargers:
                if c.Car is not None:
                    if (
                        c.Car.connectedCharger is c
                        and c.Car.vehicle_id == queue_id
                        and reduce_Wh < 0
                    ):

                        if c.ampereCharging == 0:
                            c.ampereCharging = math.ceil(float(self.ADapi.get_state(c.charging_amps,
                                namespace = c.namespace))
                            )

                        if c.ampereCharging > c.min_ampere:
                            AmpereToReduce = math.floor(reduce_Wh / c.voltPhase)
                            if (c.ampereCharging + AmpereToReduce) < c.min_ampere:
                                c.setChargingAmps(charging_amp_set = c.min_ampere)
                                available_Wh -= (c.ampereCharging  - c.min_ampere) * c.voltPhase
                                reduce_Wh -= (c.ampereCharging  - c.min_ampere) * c.voltPhase
                            else:
                                c.changeChargingAmps(charging_amp_change = AmpereToReduce)
                                available_Wh -= AmpereToReduce * c.voltPhase
                                reduce_Wh -= AmpereToReduce * c.voltPhase
                                break
                        else:
                            c.ampereCharging = math.ceil(float(self.ADapi.get_state(c.charging_amps,
                                namespace = c.namespace))
                            )
                        
        return available_Wh


    def findCharingNotInQueue(self) -> bool:
        """ Finds charger not started from queue and returns True if no software update is in progress.
        """
        softwareUpdates = False
        for c in self.cars:
            if c.getLocation() == 'home':
                if c.SoftwareUpdates():
                    softwareUpdates = True
        # Stop other chargers if a car is updating software. Not able to adjust chargespeed when updating.
        if softwareUpdates:
            for c in self.cars:
                if (
                    c.getLocation() == 'home'
                    and not c.dontStopMeNow()
                    and c.getCarChargerState() == 'Charging'
                ):
                    c.stopCharging()
            return False

        for c in self.cars:
            if (
                c.getLocation() == 'home'
                and c.getCarChargerState() == 'Charging'
                and c.vehicle_id not in self.queueChargingList
                and not self.SolarProducing_ChangeToZero
            ):
                self.queueChargingList.append(c.vehicle_id)
        return True


    def getHeatersReducedPreviousConsumption(self, available_Wh:float) -> (float, float):
        """ Function that finds the value of power consumption when heating for items that are turned down
            and turns the heating back on if there is enough available watt,
            or return how many watt to reduce charing to turn heating back on.
        """
        self.findCharingNotInQueue()
        reduce_Wh: float = 0

        for heater in reversed(self.heatersRedusedConsumption):
            if heater.prev_consumption + 600 < available_Wh:
                heater.setPreviousState()
                available_Wh -= heater.prev_consumption
                self.heatersRedusedConsumption.remove(heater)
                self.lastTimeHeaterWasReduced = datetime.datetime.now()
            elif heater.prev_consumption > available_Wh:
                reduce_Wh -= heater.prev_consumption
        return reduce_Wh, available_Wh



    def findConsumptionAfterTurnedBackOn(self, kwargs) -> None:
        """ Functions to register consumption based on outside temperature after turned back on,
            to better be able to calculate chargingtime based on max kW pr hour usage
        """
        for heater in self.heaters:
            if not heater.away_state:
                for daytime in heater.daytime_savings:
                    if 'start' in daytime and 'stop' in daytime:
                        if not 'presence' in daytime:
                            off_hours = self.ADapi.parse_datetime(daytime['stop']) - self.ADapi.parse_datetime(daytime['start'])
                            if off_hours < datetime.timedelta(minutes = 0):
                                off_hours += datetime.timedelta(days = 1)

                            hoursOffInt = off_hours.seconds//3600
                            if heater.off_for_hours < hoursOffInt:
                                heater.off_for_hours = hoursOffInt
                                turnsBackOn = self.ADapi.parse_datetime(daytime['stop'])
                                heater.turn_back_on = turnsBackOn.hour

                if datetime.datetime.now().hour < heater.turn_back_on:
                    if heater.findConsumptionAfterTurnedOn_Handler != None:
                        if self.ADapi.timer_running(heater.findConsumptionAfterTurnedOn_Handler):
                            try:
                                self.ADapi.cancel_timer(heater.findConsumptionAfterTurnedOn_Handler)
                            except Exception as e:
                                self.ADapi.log(
                                    f"Was not able to stop existing handler to findConsumptionAfterTurnedBackOn for {heater.heater}. {e}",
                                    level = "DEBUG"
                                )
                    runAt = (
                        datetime.datetime.today().replace(hour=0, minute=1, second=0, microsecond=0)
                        + datetime.timedelta(hours = heater.turn_back_on)
                    )
                    heater.findConsumptionAfterTurnedOn_Handler = self.ADapi.run_at(heater.findConsumptionAfterTurnedOn, runAt)


    # Function to find heater configuration by its name
    def check_if_heaterName_is_in_heaters(self, heater_name:str) -> bool:
        for heater in self.heaters:
            if heater_name == heater.heater:
                return True
        return False


    def calculateIdleConsumption(self, kwargs) -> None:
        """ Calculates expected available watts for each hour to calculate chargetime based on outside temperature and how many hours heaters has been off.
            The 'idleConsumption' consists of two watt measurements.
            One is all not registered from charging and heating.
            The other is all heaters combined as a average on outside temperature.
        """
        with open(JSON_PATH, 'r') as json_read:
            ElectricityData = json.load(json_read)

        if self.totalWattAllHeaters == 0:
            heaters_to_remove = []
            for heaterName in ElectricityData['consumption']:
                if heaterName != 'idleConsumption':
                    if 'power' in ElectricityData['consumption'][heaterName]:
                        self.totalWattAllHeaters += ElectricityData['consumption'][heaterName]['power']
                    if not self.check_if_heaterName_is_in_heaters(heater_name = heaterName):
                        heaters_to_remove.append(heaterName)
            if heaters_to_remove:
                # Remove old/uncofigured heaters from your JSON data
                updatedListWithCurrentHeaters:dict = {}
                for entry in ElectricityData['consumption']:
                    if entry not in heaters_to_remove:
                        updatedListWithCurrentHeaters.update({entry : ElectricityData['consumption'][entry]})
                ElectricityData['consumption'] = updatedListWithCurrentHeaters
                with open(JSON_PATH, 'w') as json_write:
                    json.dump(ElectricityData, json_write, indent = 4)

        available_Wh_toCharge:list = [self.max_kwh_usage_pr_hour*1000] * 48
        idleHeaterPercentageUsage:float = 0
        turnsBackOn:int = 0

        if ElectricityData['consumption']['idleConsumption']['ConsumptionData']:
            out_temp_str = str(math.floor(OUT_TEMP / 2.) * 2)
            try:
                closest_temp = ElectricityData['consumption']['idleConsumption']['ConsumptionData'][out_temp_str]
            except Exception:
                temp_diff:int = 100
                closest_temp:int
                for temps in ElectricityData['consumption']['idleConsumption']['ConsumptionData']:
                    if OUT_TEMP > float(temps):
                        if temp_diff < OUT_TEMP - float(temps):
                            continue
                        temp_diff = OUT_TEMP - float(temps)
                        closest_temp = temps
                    else:
                        if temp_diff < float(temps) - OUT_TEMP:
                            continue
                        temp_diff = float(temps) - OUT_TEMP
                        closest_temp = temps
                out_temp_str = str(closest_temp)

            reduceAvgHeaterwatt = float(ElectricityData['consumption']['idleConsumption']['ConsumptionData'][out_temp_str]['HeaterConsumption'])
            reduceAvgIdlewatt = float(ElectricityData['consumption']['idleConsumption']['ConsumptionData'][out_temp_str]['Consumption'])

            for watt in range(len(available_Wh_toCharge)):
                reducewatt = available_Wh_toCharge[watt]
                reducewatt -= reduceAvgHeaterwatt
                reducewatt -= reduceAvgIdlewatt
                
                available_Wh_toCharge[watt] = reducewatt


        for heaterName in ElectricityData['consumption']:
            if heaterName != 'idleConsumption':
                if ElectricityData['consumption'][heaterName]['ConsumptionData']:
                    for heater in self.heaters:
                        if heaterName == heater.heater:
                            turn_on_at = heater.turn_back_on
                            if turnsBackOn < turn_on_at:
                                turnsBackOn = turn_on_at

                            if heater.off_for_hours > 0:
                                off_for = str(heater.off_for_hours)
                                    # Find closest time registered with data
                                if off_for in ElectricityData['consumption'][heaterName]['ConsumptionData']:
                                    off_for_data = ElectricityData['consumption'][heaterName]['ConsumptionData'][off_for]
                                else:
                                    time_diff:int = 24
                                    closest_time:int
                                    for time in ElectricityData['consumption'][heaterName]['ConsumptionData']:
                                        if int(off_for) > int(time):
                                            if time_diff < int(off_for) - int(time):
                                                continue
                                            time_diff = int(off_for) - int(time)
                                            closest_time = time
                                        else:
                                            if time_diff < int(time) - int(off_for):
                                                continue
                                            time_diff = int(time) - int(off_for)
                                            closest_time = time

                                    off_for = closest_time
                                    off_for_data = ElectricityData['consumption'][heaterName]['ConsumptionData'][off_for]

                                out_temp_str = str(math.floor(OUT_TEMP / 2.) * 2)
                                    # Find closest temp registered with data
                                try:
                                    expectedHeaterConsumption = round(float(off_for_data[out_temp_str]['Consumption']) * 1000, 2)
                                except Exception:
                                    temp_diff:int = 100
                                    closest_temp:int
                                    for temps in ElectricityData['consumption'][heaterName]['ConsumptionData'][off_for]:
                                        if OUT_TEMP > float(temps):
                                            if temp_diff < OUT_TEMP - float(temps):
                                                continue
                                            temp_diff = OUT_TEMP - float(temps)
                                            closest_temp = temps
                                        else:
                                            if temp_diff < float(temps) - OUT_TEMP:
                                                continue
                                            temp_diff = float(temps) - OUT_TEMP
                                            closest_temp = temps
                                    out_temp_str = str(closest_temp)
                                    expectedHeaterConsumption = round(float(off_for_data[out_temp_str]['Consumption']) * 1000, 2)
                                
                                heaterWatt = ElectricityData['consumption'][heaterName]['power']
                                # Remove part of the calculated Idle consumption:
                                pctHeaterWatt = heaterWatt / self.totalWattAllHeaters
                                heaterWatt -= reduceAvgHeaterwatt * pctHeaterWatt

                                while (
                                    turn_on_at < len(available_Wh_toCharge)
                                    and expectedHeaterConsumption > heaterWatt
                                ):
                                    watt = available_Wh_toCharge[turn_on_at]
                                    watt -= heaterWatt
                                    if watt < 0:
                                        expectedHeaterConsumption -= watt
                                        watt = 0
                                    available_Wh_toCharge[turn_on_at] = watt
                                    expectedHeaterConsumption -= heaterWatt
                                    turn_on_at += 1
                                if expectedHeaterConsumption > 0:
                                    watt = available_Wh_toCharge[turn_on_at]
                                    watt -= expectedHeaterConsumption
                                    available_Wh_toCharge[turn_on_at] = watt

        CHARGE_SCHEDULER.turnsBackOn = turnsBackOn
        CHARGE_SCHEDULER.availableWatt = available_Wh_toCharge


    def logIdleConsumption(self, kwargs) -> None:
        """ Calculates average idle consumption and heater consumption and writes to persistent storage based on outside temperature
        """
        try:
            current_consumption = float(self.ADapi.get_state(self.current_consumption))
        except ValueError as ve:
            if self.ADapi.get_state(self.current_consumption) == 'unavailable':
                self.ADapi.log(f"Current consumption is unavailable at startup", level = 'DEBUG')
            else:
                self.ADapi.log(ve, level = 'DEBUG')
            return
        heater_consumption:float = 0.0
        for heater in self.heaters:
            if heater.validConsumptionSensor:
                try:
                    heater_consumption += float(self.ADapi.get_state(heater.consumptionSensor,
                        namespace = heater.namespace)
                    )
                except ValueError:
                    pass

        idle_consumption = current_consumption - heater_consumption
        if idle_consumption <= 0:
            idle_consumption = 0.1

        with open(JSON_PATH, 'r') as json_read:
            ElectricityData = json.load(json_read)

        out_temp_str = str(math.floor(OUT_TEMP / 2.) * 2)

        # TODO: Verify consumption is within normal range

        if not out_temp_str in ElectricityData['consumption']['idleConsumption']['ConsumptionData']:
            newData = {"Consumption" : round(idle_consumption,2),"HeaterConsumption" : round(heater_consumption,2), "Counter" : 1}
            ElectricityData['consumption']['idleConsumption']['ConsumptionData'].update({out_temp_str : newData})
        else:
            consumptionData = ElectricityData['consumption']['idleConsumption']['ConsumptionData'][out_temp_str]
            counter = consumptionData['Counter'] + 1
            avgConsumption = round(((consumptionData['Consumption'] * consumptionData['Counter']) + idle_consumption) / counter,2)
            avgHeaterConsumption = round(((consumptionData['HeaterConsumption'] * consumptionData['Counter']) + heater_consumption) / counter,2)
            if counter > 100:
                counter = 10
            newData = {"Consumption" : avgConsumption, "HeaterConsumption" : avgHeaterConsumption, "Counter" : counter}
            ElectricityData['consumption']['idleConsumption']['ConsumptionData'].update({out_temp_str : newData})

        with open(JSON_PATH, 'w') as json_write:
            json.dump(ElectricityData, json_write, indent = 4)


    def logHighUsage(self) -> None:
        """ Writes top three max kWh usage pr hour to persistent storage
        """
        newTotal = 0.0
        with open(JSON_PATH, 'r') as json_read:
            ElectricityData = json.load(json_read)
        max_kwh_usage_top = ElectricityData['MaxUsage']['topUsage']
        newTopUsage:float = 0

        try:
            newTopUsage = float(self.ADapi.get_state(self.accumulated_consumption_current_hour))
            if newTopUsage > max_kwh_usage_top[0]:
                max_kwh_usage_top[0] = newTopUsage
                ElectricityData['MaxUsage']['topUsage'] = sorted(max_kwh_usage_top)
            self.top_usage_hour = ElectricityData['MaxUsage']['topUsage'][0]
        except ValueError as ve:
            self.ADapi.log(
                f"Not able to set new Top Hour Usage. Accumulated consumption is {self.ADapi.get_state(self.accumulated_consumption_current_hour)} "
                f"ValueError: {ve}",
                level = 'WARNING'
            )
        except Exception as e:
            self.ADapi.log(f"Not able to set new Top Hour Usage. Exception: {e}", level = 'WARNING')

        for num in ElectricityData['MaxUsage']['topUsage']:
            newTotal += num
        avg_top_usage = newTotal / 3

        if avg_top_usage > self.max_kwh_usage_pr_hour:
            self.max_kwh_usage_pr_hour += 5
            ElectricityData['MaxUsage']['max_kwh_usage_pr_hour'] = self.max_kwh_usage_pr_hour 
            self.ADapi.log(
                f"Avg consumption during one hour is now {round(avg_top_usage, 3)} kWh and surpassed max kWh set. "
                f"New max kWh usage during one hour set to {self.max_kwh_usage_pr_hour}. "
                "If this is not expected try to increase buffer.",
                level = 'WARNING'
            )
        elif (
            avg_top_usage > self.max_kwh_usage_pr_hour - self.buffer
            and newTopUsage != 0   
        ):
            self.ADapi.log(
                f"Consumption last hour: {round(newTopUsage, 3)}. "
                f"Avg top 3 hours: {round(avg_top_usage, 3)}",
                level = 'INFO'
            )

        with open(JSON_PATH, 'w') as json_write:
            json.dump(ElectricityData, json_write, indent = 4)


    def resetHighUsage(self) -> None:
        """ Resets max usage pr hour for new month
        """
        with open(JSON_PATH, 'r') as json_read:
            ElectricityData = json.load(json_read)
        self.max_kwh_usage_pr_hour = self.max_kwh_goal
        ElectricityData['MaxUsage']['max_kwh_usage_pr_hour'] = self.max_kwh_usage_pr_hour
        ElectricityData['MaxUsage']['topUsage'] = [0,0,float(self.ADapi.get_state(self.accumulated_consumption_current_hour))]

        with open(JSON_PATH, 'w') as json_write:
            json.dump(ElectricityData, json_write, indent = 4)


        # Set proper value when weather sensors is updated
    def weather_event(self, event_name, data, kwargs) -> None:
        """ Listens for weather change from the weather app
        """
        global OUT_TEMP
        global RAIN_AMOUNT
        global WIND_AMOUNT

        if self.ADapi.datetime(aware=True) - self.out_temp_last_update > datetime.timedelta(minutes = 20):
            OUT_TEMP = data['temp']
        if self.ADapi.datetime(aware=True) - self.rain_last_update > datetime.timedelta(minutes = 20):
            RAIN_AMOUNT = data['rain']
        if self.ADapi.datetime(aware=True) - self.wind_last_update > datetime.timedelta(minutes = 20):
            WIND_AMOUNT = data['wind']


    def outsideTemperatureUpdated(self, entity, attribute, old, new, kwargs) -> None:
        """ Updates OUT_TEMP from sensor
        """
        global OUT_TEMP
        try:
            OUT_TEMP = float(new)
        except (ValueError, TypeError) as ve:
            pass
        else:
            self.out_temp_last_update = self.ADapi.datetime(aware=True)


    def rainSensorUpdated(self, entity, attribute, old, new, kwargs) -> None:
        """ Updates RAIN_AMOUNT from sensor
        """
        global RAIN_AMOUNT
        try:
            RAIN_AMOUNT = float(new)
        except ValueError as ve:
            RAIN_AMOUNT = 0.0
            self.ADapi.log(f"Not able to set new rain amount: {new}. {ve}", level = 'DEBUG')
        else:
            self.rain_last_update = self.ADapi.datetime(aware=True)
        

    def anemometerUpdated(self, entity, attribute, old, new, kwargs) -> None:
        """ Updates WIND_AMOUNT from sensor
        """
        global WIND_AMOUNT
        try:
            WIND_AMOUNT = float(new)
        except ValueError as ve:
            WIND_AMOUNT = 0.0
            self.ADapi.log(f"Not able to set new wind amount: {new}. {ve}", level = 'DEBUG')
        else:
            self.wind_last_update = self.ADapi.datetime(aware=True)


    def mode_event(self, event_name, data, kwargs) -> None:
        """ Listens to same mode event that I have used in Lightwand: https://github.com/Pythm/ad-Lightwand
            If mode name equals 'fire' it will turn off all charging and heating.
            To call from another app use: self.fire_event("MODE_CHANGE", mode = 'fire')
            Set back to normal with mode 'false_alarm'.
        """
        if data['mode'] == 'fire':
            self.houseIsOnFire = True
            for c in self.cars:
                if (
                    c.getLocation() == 'home'
                    and c.getCarChargerState() == 'Charging'
                ):
                    c.stopCharging()
            
            for charger in self.chargers:
                charger.doNotStartMe = True

            for heater in self.heaters:
                heater.turn_off_heater()


        elif data['mode'] == 'false_alarm':
            # Fire alarm stopped
            self.houseIsOnFire = False
            for heater in self.heaters:
                heater.turn_on_heater()
            
            for charger in self.chargers:
                charger.doNotStartMe = False

            for c in self.cars:
                if c.kWhRemaining() > 0:
                    c.findNewChargeTime()


class Scheduler:
    """ Class for calculating and schedule charge times
    """

    def __init__(self, api,
        stopAtPriceIncrease:float,
        startBeforePrice:float,
        infotext,
        namespace:str
    ):
        self.ADapi = api
        self.namespace = namespace
        self.stopAtPriceIncrease = stopAtPriceIncrease
        self.startBeforePrice = startBeforePrice

        # Helpers
        self.chargingQueue:list = []
        self.informHandler = None
        self.infotext = infotext
       
        # Is updated from main class when turning off/down to save on electricity price
        self.turnsBackOn:int = 22
        self.availableWatt:list = []


    def calculateChargingTimes(self, kWhRemaining:float, totalW_AllChargers:float) -> int:
        """ Calculates expected charging time based on available power.
            Takes into consideration max kWh usage and logged usage based on outside temperature.
        """
        if self.availableWatt:
            h = self.turnsBackOn
            hoursToCharge = 0
            WhRemaining = kWhRemaining * 1000
            while (
                h < len(self.availableWatt)
                and WhRemaining > 0
            ):
                if self.availableWatt[h] < totalW_AllChargers:
                    WhRemaining -= self.availableWatt[h]
                else:
                    WhRemaining -= totalW_AllChargers
                h += 1
                hoursToCharge += 1
            return hoursToCharge

        return math.ceil(kWhRemaining / (totalW_AllChargers / 1000))


    def getCharingTime(self, vehicle_id:str) -> (datetime, datetime):
        """ Helpers used to return data. Returns charging start and stop time for vehicle_id
        """
        for c in self.chargingQueue:
            if vehicle_id == c['vehicle_id']:
                if (
                    'chargingStart' in c
                    and 'chargingStop' in c
                ):
                    return c['chargingStart'], c['chargingStop']
        return None, None


    def isChargingTime(self, vehicle_id:str = None) -> bool:
        """ Helpers used to return data. Returns True if it it chargingtime
        """
        price = 0
        for c in self.chargingQueue:
            if (
                vehicle_id == None
                or vehicle_id == c['vehicle_id']
            ):
                if (
                    'chargingStart' in c
                    and 'chargingStop' in c
                ):
                    if c['chargingStart'] != None:
                        if (
                            datetime.datetime.today() >= c['chargingStart']
                            and datetime.datetime.today() < c['chargingStop']
                        ):
                            return True

                    if 'price' in c:
                        price = c['price']
        if (
            self.ADapi.now_is_between('07:00:00', '14:00:00')
            and len(ELECTRICITYPRICE.elpricestoday) == 24
            and self.chargingQueue
        ):
            # Finds low price during day awaiting tomorrows prices
            # TODO: A better logic to charge if price is lower than usual before tomorrow prices is available from Nordpool.

            calculatePrice:bool = False
            price:float = 0
            for c in self.chargingQueue:
                if not 'price' in c:
                    calculatePrice = True
                else:
                    price = c['price']

            if calculatePrice:
                kWhToCharge = 0
                totalW_AllChargers = 0
                for c in self.chargingQueue:
                    kWhToCharge += c['kWhRemaining']
                    totalW_AllChargers += c['maxAmps'] * c['voltPhase']
                hoursToCharge = self.calculateChargingTimes(kWhRemaining = kWhToCharge, totalW_AllChargers = totalW_AllChargers)
                price = ELECTRICITYPRICE.sorted_elprices_today[hoursToCharge]

            for c in self.chargingQueue:
                c['price'] = price

        return ELECTRICITYPRICE.elpricestoday[datetime.datetime.today().hour] <= price

    def isPastChargingTime(self, vehicle_id:str = None) -> bool:
        """ Helpers used to return data. Returns True if it is past chargingtime.
        """
        for c in self.chargingQueue:
            if vehicle_id == c['vehicle_id']:
                if 'chargingStop' in c:
                    if c['chargingStop'] == None:
                        return True
                    return datetime.datetime.today() > c['chargingStop']
        return True


    def hasChargingScheduled(self, vehicle_id:str) -> bool:
        """ Helpers used to return data. Returns True if vehicle_id has charging scheduled.
        """
        for c in self.chargingQueue:
            if vehicle_id == c['vehicle_id']:
                return True
        return False

    def findChargerToStart(self) -> str:
        """ Helpers used to return data. Returns first vehicle_id that has charging scheduled.
        """
        pri = 1
        while pri < 5:
            for c in self.chargingQueue:
                if c['priority'] == pri:
                    if self.isChargingTime(vehicle_id = c['vehicle_id']):
                        return c['vehicle_id']
            pri += 1
        return None

    def findNextChargerToStart(self) -> str:
        """ Helpers used to return data. Returns next vehicle_id that has charging scheduled.
        """
        foundFirst = False
        pri = 1
        while pri < 5:
            for c in self.chargingQueue:
                if c['priority'] == pri:
                    if self.isChargingTime(vehicle_id = c['vehicle_id']):
                        if not foundFirst:
                            foundFirst = True
                        else:
                            return c['vehicle_id']
            pri += 1
        return None


    def removeFromQueue(self, vehicle_id:str) -> None:
        """ Removes a charger from queue after finished charging or disconnected.
        """
        for c in self.chargingQueue:
            if vehicle_id == c['vehicle_id']:
                self.chargingQueue.remove(c)


    def queueForCharging(self,
        vehicle_id:str,
        kWhRemaining:float,
        maxAmps:int,
        voltPhase:int,
        finishByHour:int,
        priority:int,
        name:str
    ) -> bool:
        """ Adds charger to queue and sets charging time, Returns True if it is charging time.
        """
        if kWhRemaining <= 0:
            self.removeFromQueue(vehicle_id = vehicle_id)
            return False

        if self.hasChargingScheduled(vehicle_id = vehicle_id):
            for c in self.chargingQueue:
                if vehicle_id == c['vehicle_id']:
                    if (
                        c['kWhRemaining'] == kWhRemaining
                        and c['finishByHour'] == finishByHour
                    ):
                        if 'chargingStart' in c:
                            if datetime.datetime.today() < c['chargingStart']:
                                return self.isChargingTime(vehicle_id = c['vehicle_id'])
                    else:
                        c['kWhRemaining'] = kWhRemaining
                        c['finishByHour'] = finishByHour
                        c['estHourCharge'] = self.calculateChargingTimes(
                            kWhRemaining = c['kWhRemaining'],
                            totalW_AllChargers = c['maxAmps'] * c['voltPhase']
                        )

        else:
            estHourCharge = self.calculateChargingTimes(
                kWhRemaining = kWhRemaining,
                totalW_AllChargers = maxAmps * voltPhase
            ) 
            self.chargingQueue.append({'vehicle_id' : vehicle_id,
                'kWhRemaining' : kWhRemaining,
                'maxAmps' : maxAmps,
                'voltPhase' : voltPhase,
                'finishByHour' : finishByHour,
                'priority' : priority,
                'estHourCharge' : estHourCharge,
                'name' : name})

        if (
            self.ADapi.now_is_between('07:00:00', '14:00:00')
            and len(ELECTRICITYPRICE.elpricestoday) == 24
        ):
            return self.isChargingTime(vehicle_id = vehicle_id)


        simultaneousCharge:list = []
        simultaneousChargeComplete:list = []
        prev_id:str = ""
        prev_Stop = None

        def by_value(item):
            return item['finishByHour']
        for c in sorted(self.chargingQueue, key=by_value):

            ChargingAt, c['estimateStop'], c['price'] = ELECTRICITYPRICE.getContinuousCheapestTime(
                hoursTotal = c['estHourCharge'],
                calculateBeforeNextDayPrices = False,
                finishByHour = c['finishByHour']
            )

            if (
                ChargingAt is not None
                and c['estimateStop'] is not None
            ):
                c['chargingStart'], c['estimateStop'] = self.CheckChargingStartTime(
                    ChargingAt = ChargingAt,
                    EndAt = c['estimateStop'],
                    price = c['price']
                )

                if prev_Stop == None:
                    prev_id = c['vehicle_id']
                    prev_Stop = c['estimateStop']

                else:
                    if c['chargingStart'] < prev_Stop:
                        if not prev_id in simultaneousCharge:
                            simultaneousCharge.append(prev_id)
                        if not c['vehicle_id'] in simultaneousCharge:
                            simultaneousCharge.append(c['vehicle_id'])

                    else:
                        if simultaneousCharge:
                            self.calcSimultaneousCharge(
                                simultaneousCharge= simultaneousCharge
                            )
                            simultaneousChargeComplete.extend(simultaneousCharge)
                            simultaneousCharge = []
                        
            prev_id = c['vehicle_id']
            prev_Stop = c['estimateStop']

        if simultaneousCharge:
            self.calcSimultaneousCharge(
                simultaneousCharge= simultaneousCharge
            )

            simultaneousChargeComplete.extend(simultaneousCharge)
            simultaneousCharge = []

        for c in self.chargingQueue:
            if (
                c['vehicle_id'] not in simultaneousChargeComplete
                and c['chargingStart'] is not None
            ):
                c['chargingStop'] = self.extendChargingTime(
                    EndAt =  c['estimateStop'],
                    price = c['price']
                )

        return self.isChargingTime(vehicle_id = vehicle_id)


    def calcSimultaneousCharge(self, simultaneousCharge:list):
        """ Calculates charging time for vehicles that has the same charging time.
        """
        finishByHour:int = 0
        kWhToCharge:float = 0.0
        totalW_AllChargers:float = 0.0
        for c in self.chargingQueue:
            if c['vehicle_id'] in simultaneousCharge:
                kWhToCharge += c['kWhRemaining']
                totalW_AllChargers += c['maxAmps'] * c['voltPhase']
                if c['finishByHour'] > finishByHour:
                    finishByHour = c['finishByHour']


        hoursToCharge = self.calculateChargingTimes(
            kWhRemaining = kWhToCharge,
            totalW_AllChargers = totalW_AllChargers
        )

        ChargingAt, estimateStop, price = ELECTRICITYPRICE.getContinuousCheapestTime(
            hoursTotal = hoursToCharge,
            calculateBeforeNextDayPrices = False,
            finishByHour = finishByHour
        )

        if estimateStop != None:
            charging_Start, estimateStop = self.CheckChargingStartTime(
                    ChargingAt = ChargingAt,
                    EndAt = estimateStop,
                    price = c['price']
                )

            charging_Stop = self.extendChargingTime(
                EndAt = estimateStop,
                price = price
            )

            for c in self.chargingQueue:
                if c['vehicle_id'] in simultaneousCharge:
                    c['chargingStart'] = charging_Start
                    c['estimateStop'] = estimateStop
                    c['chargingStop'] = charging_Stop


    def extendChargingTime(self, EndAt, price) -> datetime:
        """ Extends charging time after estimated finish as long as price is lower than stopAtPriceIncrease
        """
        EndChargingHour = EndAt.hour
        if EndAt.day - 1 == datetime.datetime.today().day:
            EndChargingHour += 24

        # Check when charging needs to stop be cause of price increase
        while (
            EndChargingHour < len(ELECTRICITYPRICE.elpricestoday) -1
            and price + self.stopAtPriceIncrease > ELECTRICITYPRICE.elpricestoday[EndChargingHour]
        ):
            EndChargingHour += 1
            EndAt += datetime.timedelta(hours = 1)

        EndAt = EndAt.replace(minute = 0, second = 0, microsecond = 0)

        return EndAt

    def CheckChargingStartTime(self, ChargingAt, EndAt, price) -> datetime:
        """ Check if charging should be postponed one hour or start earlier due to price.
        """
        StartChargingHour = ChargingAt.hour
        if ChargingAt.day - 1 == datetime.datetime.today().day:
            StartChargingHour += 24
        startHourPrice = ELECTRICITYPRICE.elpricestoday[StartChargingHour]

        if (
            price < startHourPrice - (self.stopAtPriceIncrease * 1.5)
            and startHourPrice < ELECTRICITYPRICE.elpricestoday[StartChargingHour+1] - (self.stopAtPriceIncrease * 1.3)
        ):
            ChargingAt += datetime.timedelta(hours = 1)
        else:
            hoursToChargeStart = ChargingAt - datetime.datetime.today().replace(second = 0, microsecond = 0)
            hoursToStart = hoursToChargeStart.seconds//3600

            while (
                hoursToStart > 0
                and startHourPrice + self.startBeforePrice >= ELECTRICITYPRICE.elpricestoday[StartChargingHour-1]
                and price + (self.startBeforePrice * 2) >= ELECTRICITYPRICE.elpricestoday[StartChargingHour-1]
            ):
                StartChargingHour -= 1
                hoursToStart -= 1
                ChargingAt -= datetime.timedelta(hours = 1)
                EndAt -= datetime.timedelta(hours = 1)

        return ChargingAt, EndAt


    def notifyChargeTime(self, kwargs):
        """ Sends notifications and updates infotext with charging times and prices.
        """

        price = None
        timesSet = False
        infotxt:str = ""
        send_new_info:bool = False

        self.informedStart = datetime.datetime.today()
        self.informedStop = datetime.datetime.today()
        
        # Notify about chargetime
        def by_value(item):
            return item['finishByHour']
        for c in sorted(self.chargingQueue, key=by_value):
            if self.hasChargingScheduled(vehicle_id = c['vehicle_id']):
                if (
                    'informedStart' in c
                    and 'informedStop' in c
                    and 'chargingStart' in c
                ):
                    
                    if (
                        c['informedStart'] != c['chargingStart']
                        or c['informedStop'] != c['estimateStop']
                    ):
                        send_new_info = True
                else:
                    send_new_info = True

                if 'chargingStart' in c:
                    if c['chargingStart'] != None:
                        c['informedStart'] = c['chargingStart']
                        c['informedStop'] = c['estimateStop']

                        infotxt += f"Start {c['name']} at {c['chargingStart']}. Finish est at {c['estimateStop']}. Stop no later than {c['chargingStop']}. "
                        timesSet = True
                if 'price' in c:
                    if c['price'] != None:
                        if price == None:
                            price = c['price']
                        elif price < c['price']:
                            price = c['price']

        # Notify about price before chargetime is set (Waiting for next days prices)
        if not timesSet:
            if price == None:
                self.isChargingTime()
                for c in self.chargingQueue:
                    if 'price' in c:
                        if c['price'] != None:
                            price = c['price']

            if price != None:
                infotxt = (
                    f"Charge if price is lower than {ELECTRICITYPRICE.currency} {round(price - ELECTRICITYPRICE.daytax,3)} (day) "
                    f"or {ELECTRICITYPRICE.currency} {round(price - ELECTRICITYPRICE.nighttax,3)} (night/weekend)"
                )
                send_new_info = True

        if send_new_info:
            data = {
                'tag' : 'chargequeue'
                }
            NOTIFY_APP.send_notification(
                message = infotxt,
                message_title = f"🔋 Charge Queue",
                message_recipient = RECIPIENTS,
                also_if_not_home = True,
                data = data
            )
        if self.infotext:
            self.ADapi.call_service('input_text/set_value',
                value = infotxt,
                entity_id = self.infotext,
                namespace = self.namespace
            )


class Charger:
    """ Charger parent class
    Set variables in childclass before init:
        self.charger_id:str # Unik ID to identify chargers
        self.volts:int # 220/266/400v
        self.phases:int # 1 phase or 3 phase
        self.cars:list # list of cars that can connect to charger

    Set variables in childclass after init for connected chargers if needed:
        self.guestCharging:bool # Defaults to False
    Set variables in childclass after init for onboard chargers if needed:
        self.Car
        self.Car.onboardCharger
    
    Change default values if needed:
    self.min_ampere = 6
    
    Functions to implement in child class:
        def setmaxChargingAmps(self) -> None:
        def getChargingState(self) -> str:
    """

    def __init__(self, api,
        namespace:str,
        charger:str, # Name of your charger.
        charger_sensor:str, # Cable Connected or Disconnected
        charger_switch:str, # Charging or not
        charging_amps:str, # Ampere charging
        charger_power:str, # Charger power
        session_energy:str # Charged this session in kWh
    ):

        self.ADapi = api
        self.Car = None
        self.namespace = namespace

        # Sensors
        self.charger = charger
        self.charger_sensor = charger_sensor
        self.charger_switch = charger_switch
        self.charging_amps = charging_amps
        self.charger_power = charger_power
        self.session_energy = session_energy

        # Helpers
        self.guestCharging:bool = False
        self.ampereCharging:int = 0
        self.min_ampere:int = 6
        self.maxChargerAmpere:int = 0
        self.voltPhase:int = 0
        self.checkCharging_handler = None
        self.connected_Handlers:list = []
        self.doNotStartMe:bool = False
        self.pct_start_charge:float = 100

        # Check that charger exists and get data from persistent json file if so.
        with open(JSON_PATH, 'r') as json_read:
            ElectricityData = json.load(json_read)
        if not self.charger_id in ElectricityData['charger']:
            # Try to set valid data
            self.setmaxChargingAmps()
            if self.voltPhase == 0:
                self.voltPhase = 220
            if self.maxChargerAmpere == 0:
                self.maxChargerAmpere = 32
            
            ElectricityData['charger'].update(
                {self.charger_id : {
                    "voltPhase" : self.voltPhase,
                    "MaxAmp" : self.maxChargerAmpere
                }}
            )

            with open(JSON_PATH, 'w') as json_write:
                json.dump(ElectricityData, json_write, indent = 4)
        else:
            if 'voltPhase' in ElectricityData['charger'][self.charger_id]:
                self.voltPhase = int(ElectricityData['charger'][self.charger_id]['voltPhase'])
            else:
                self.setVoltPhase(
                    volts = self.volts,
                    phases = self.phases
                )
            if 'MaxAmp' in ElectricityData['charger'][self.charger_id]:
                self.maxChargerAmpere = int(ElectricityData['charger'][self.charger_id]['MaxAmp'])
            if 'ConnectedCar' in ElectricityData['charger'][self.charger_id]:
                if ElectricityData['charger'][self.charger_id]['ConnectedCar'] != None:
                    for car in self.cars:
                        if car.carName == ElectricityData['charger'][self.charger_id]['ConnectedCar']:
                            if car.isConnected():
                                car.connectedCharger = self
                                self.Car = car
                            break

        if charging_amps != None:
            api.listen_state(self.updateAmpereCharging, charging_amps,
                namespace = namespace
            )

        """ End initialization Charger Class
        """


    def findCarConnectedToCharger(self) -> bool:
        """ A check to see if a car is connected to the charger.
            Needs to be updated to support connected cars when added.
        """
        for connected_handler in self.connected_Handlers:
            if connected_handler != None:
                try:
                    self.ADapi.cancel_listen_state(connected_handler)
                except Exception as exc:
                    self.ADapi.log(
                        f"Could not stop hander listening for connection {connected_handler}. "
                        f"Exception: {exc}",
                        level = 'DEBUG'
                    )
        self.connected_Handlers = []

        if (
            self.getChargingState() != 'Disconnected'
            and self.getChargingState() != 'Complete'
            and self.getChargingState() != 'NoPower'
        ):
            if not self.guestCharging:
                for car in self.cars:

                    if car.polling_of_data():
                        if (
                            car.getLocation() == 'home'
                            and car.connectedCharger is None
                        ):
                            if type(car).__name__ == 'Tesla_car':
                                if self.compareChargingState(
                                    car_status = car.getCarChargerState()
                                ):
                                    car.connectedCharger = self
                                    self.Car = car
                                    if (
                                        not self.Car.hasChargingScheduled()
                                        and self.kWhRemaining() > 0
                                    ):
                                        self.Car.findNewChargeTime()
                                    return True

                            elif type(car).__name__ == 'Car':
                                # Generic car.
                                if car.isConnected():
                                    car.connectedCharger = self
                                    self.Car = car
                                    if (
                                        not self.Car.hasChargingScheduled()
                                        and self.kWhRemaining() > 0
                                    ):
                                        self.Car.findNewChargeTime()
                                    return True

            else: 
                self.startGuestCharging()
                return True

            for car in self.cars:
                if car.car_charger_sensor:
                    connected_handler = self.ADapi.listen_state(self.ChargeCableConnected, car.car_charger_sensor,
                        namespace = car.namespace,
                        new = 'on'
                    )
                    self.connected_Handlers.append(connected_handler)
        return False


        # Functions to react to charger sensors
    def ChargeCableConnected(self, entity, attribute, old, new, kwargs) -> None:
        """ Charge cable connected for charger.
        """
        for connected_handler in self.connected_Handlers:
            if connected_handler != None:
                try:
                    self.ADapi.cancel_listen_state(connected_handler)
                except Exception as exc:
                    self.ADapi.log(
                        f"Could not stop hander listening for connection {connected_handler}. "
                        f"Exception: {exc}",
                        level = 'DEBUG'
                    )
        self.connected_Handlers = []

        self.findCarConnectedToCharger()


    def kWhRemaining(self) -> float:
        """ Calculates kWh remaining to charge from car battery sensor/size and charge limit.
            If those are not available it uses session energy to estimate how much is needed to charge.
        """
        if self.Car is not None:
            kWhRemain:float = self.Car.kWhRemaining()
            if kWhRemain > -2:
                return kWhRemain

        if (
            self.getChargingState() == 'Complete'
            or self.getChargingState() == 'Disconnected'
        ):
            return -1

        elif self.session_energy:
            if self.guestCharging:
                return 100 - (float(self.ADapi.get_state(self.session_energy, namespace = self.namespace)))

            elif self.Car is not None:
                self.Car.kWhRemainToCharge = self.Car.maxkWhCharged - float(self.ADapi.get_state(self.session_energy,
                    namespace = self.namespace)
                )
                return self.Car.kWhRemainToCharge
        
        return -1


    def compareChargingState(self, car_status:str) -> bool:
        """ Returns True if car and charger match charging state.
        """
        charger_status = self.getChargingState()
        return car_status == charger_status


    def getChargingState(self) -> str:
        """ Returns the charging state of the charger.
            Valid returns: 'Complete' / None / 'Stopped' / 'Charging' / 'Disconnected' / 'Starting' / 'NoPower'.
        """
        if self.charger_sensor != None:
            if self.ADapi.get_state(self.charger_sensor, namespace = self.namespace) == 'on':
                # Connected
                if self.charger_switch != None:
                    if self.ADapi.get_state(self.charger_switch, namespace = self.namespace) == 'on':
                        return 'Charging'
                    elif self.kWhRemaining() > 0:
                        return 'Stopped'
                    else:
                        return "Complete"
                return 'Stopped'
            return 'Disconnected'
        return None


    def getChargerPower(self) -> float:
        """ Returns charger power in kWh.
        """
        pwr = self.ADapi.get_state(self.charger_power,
            namespace = self.namespace
        )
        try:
            pwr = float(pwr)
        except (ValueError, TypeError) as ve:
            self.ADapi.log(
                f"{self.charger} Could not get charger_power: {pwr} Error: {ve}",
                level = 'DEBUG'
            )
            pwr = 0
        except Exception as e:
            self.ADapi.log(
                f"{self.charger} Could not get charger_power: {pwr} Exception: {e}",
                level = 'WARNING'
            )
            pwr = 0
        return pwr


    def setmaxChargingAmps(self) -> None:
        """ Set maxChargerAmpere from charger sensors
        """
        self.maxChargerAmpere = 32
        self.ADapi.log(
            f"Setting maxChargerAmpere to 32. Needs to set value in child class of charger.",
            level = 'WARNING'
        )
        # Update Voltphase calculations
        self.setVoltPhase(
            volts = self.volts,
            phases = self.phases
        )


    def getmaxChargingAmps(self) -> int:
        """ Returns the maximum ampere the car/charger can get/deliver.
        """
        if self.Car is not None:
            if self.Car.car_limit_max_charging == 0:
                self.Car.car_limit_max_charging = self.maxChargerAmpere

            if self.maxChargerAmpere > self.Car.car_limit_max_charging:
                return self.Car.car_limit_max_charging
        return self.maxChargerAmpere


    def isChargingAtMaxAmps(self) -> bool:
        """ Returns True if the charging speed is at maximum.
        """
        if self.getmaxChargingAmps() <= self.ampereCharging:
            return True
        return False


    def updateAmpereCharging(self, entity, attribute, old, new, kwargs) -> None:
        """ Updates the charging ampere value in self.ampereCharging from charging_amps sensor.
        """
        try:
            newAmp = math.ceil(float(new))
        except (ValueError, TypeError) as ve:
            self.ADapi.log(
                f"{self.charger} Not able to get ampere charging. New is {new}. Error {ve}",
                level = 'DEBUG'
            )
            return
        except Exception as e:
            self.ADapi.log(
                f"{self.charger} Not able to get ampere charging. New is {new}. Exception {e}",
                level = 'WARNING'
            )
            return

        if newAmp >= 0:
            self.ampereCharging = newAmp


    def changeChargingAmps(self, charging_amp_change:int = 0) -> None:
        """ Function to change ampere charging +/-
        """
        if charging_amp_change != 0:
            new_charging_amp = self.ampereCharging + charging_amp_change
            self.setChargingAmps(charging_amp_set = new_charging_amp)


    def setChargingAmps(self, charging_amp_set:int = 16) -> int:
        """ Function to set ampere charging to received value.
            returns actual restricted within min/max ampere.
        """
        max_available_amps = self.getmaxChargingAmps()
        if charging_amp_set > max_available_amps:
            charging_amp_set = max_available_amps
        elif charging_amp_set < self.min_ampere:
            charging_amp_set = self.min_ampere

        stack = inspect.stack() # Check if called from child
        if stack[1].function != 'setChargingAmps':
            self.ampereCharging = charging_amp_set
            self.ADapi.call_service('number/set_value',
                value = self.ampereCharging,
                entity_id = self.charging_amps,
                namespace = self.namespace
            )
        return charging_amp_set


    def ChargingConnected(self, entity, attribute, old, new, kwargs) -> None:
        """ Function that reacts to charger_sensor connected or disconnected
        """
        self.ADapi.log(
            f"ChargingConnected not implemented in parent class for {self.charger}",
            level = 'WARNING'
        )


    def startCharging(self) -> bool:
        """ Starts charger.
            Parent class returns boolen to child if ready to start charging.
        """
        if self.checkCharging_handler != None:
            if self.ADapi.timer_running(self.checkCharging_handler):
                try:
                    self.ADapi.cancel_timer(self.checkCharging_handler)
                except Exception as e:
                    self.ADapi.log(
                        f"Not possible to stop timer to check if charging started/stopped. Exception: {e}",
                        level = 'DEBUG'
                    )
        if self.doNotStartMe:
            self.checkCharging_handler = None
            return False
        self.checkCharging_handler = self.ADapi.run_in(self.checkIfChargingStarted, 60)

        # Calculations for battery size:
        if (
            self.Car is not None
            and self.session_energy != None
        ):
            if (
                float(self.ADapi.get_state(self.session_energy, namespace = self.namespace)) < 2
                and self.Car.battery_sensor != None
            ):
                self.pct_start_charge = float(self.ADapi.get_state(self.Car.battery_sensor, namespace = self.namespace))

        stack = inspect.stack() # Check if called from child
        if stack[1].function == 'startCharging':
            return True
        else:
            self.ADapi.call_service('switch/turn_on',
                entity_id = self.charger_switch,
                namespace = self.namespace,
            )

        return False


    def stopCharging(self) -> bool:
        """ Stops charger.
            Parent class returns boolen to child if able to stop charging.
        """
        if self.Car is not None:
            if self.Car.dontStopMeNow():
                return False
        if (
            (self.getChargingState() == 'Charging'
            or self.getChargingState() == 'Starting')
            and not self.guestCharging
        ):
            if self.checkCharging_handler != None:
                if self.ADapi.timer_running(self.checkCharging_handler):
                    try:
                        self.ADapi.cancel_timer(self.checkCharging_handler)
                    except Exception as e:
                        self.ADapi.log(
                            f"Not possible to stop timer to check if charging started/stopped. Exception: {e}",
                            level = 'DEBUG'
                        )

            self.setmaxChargingAmps()
            self.checkCharging_handler = self.ADapi.run_in(self.checkIfChargingStopped, 60)

            stack = inspect.stack() # Check if called from child
            if stack[1].function == 'stopCharging':
                return True
            else:
                self.ADapi.call_service('switch/turn_off',
                    entity_id = self.charger_switch,
                    namespace = self.namespace,
                )

        return False


    def checkIfChargingStarted(self, kwargs) -> bool:
        """ Check if charger was able to start.
        """
        if not self.getChargingState() in ['Charging', 'Complete', 'Disconnected']:
            if self.ADapi.timer_running(self.checkCharging_handler):
                try:
                    self.ADapi.cancel_timer(self.checkCharging_handler)
                except Exception as e:
                    self.ADapi.log(
                        f"Not possible to stop timer to check if charging started/stopped. Exception: {e}",
                        level = 'DEBUG'
                    )

            self.checkCharging_handler = self.ADapi.run_in(self.checkIfChargingStarted, 60)

            stack = inspect.stack() # Check if called from child
            if (
                stack[1].function == 'startCharging'
                or stack[1].function == 'checkIfChargingStarted'
            ):
                return False
            else:
                self.ADapi.call_service('switch/turn_on',
                    entity_id = self.charger_switch,
                    namespace = self.namespace,
                )

        return True


    def checkIfChargingStopped(self, kwargs) -> bool:
        """ Check if charger was able to stop.
        """
        if self.Car is not None:
            if self.Car.dontStopMeNow():
                return True
            if self.getChargingState() == 'Charging':
                if self.ADapi.timer_running(self.checkCharging_handler):
                    try:
                        self.ADapi.cancel_timer(self.checkCharging_handler)
                    except Exception as e:
                        self.ADapi.log(
                            f"Not possible to stop timer to check if charging started/stopped. Exception: {e}",
                            level = 'DEBUG'
                        )

                self.checkCharging_handler = self.ADapi.run_in(self.checkIfChargingStopped, 60)

                stack = inspect.stack() # Check if called from child
                if (
                    stack[1].function == 'stopCharging'
                    or stack[1].function == 'checkIfChargingStopped'
                ):
                    return False
                else:
                    self.ADapi.call_service('switch/turn_off',
                        entity_id = self.charger_switch,
                        namespace = self.namespace,
                    )

        return True


    def ChargingStarted(self, entity, attribute, old, new, kwargs) -> None:
        """ Charger started charging. Check if controlling car and if chargetime has been set up
        """
        if self.Car is None:
            return

        if self.Car.connectedCharger is None:
            if not self.findCarConnectedToCharger():
                return
        
        if self.Car.connectedCharger is self:
            if not self.Car.hasChargingScheduled():
                if self.kWhRemaining() > 0:
                    self.Car.findNewChargeTime()


    def ChargingStopped(self, entity, attribute, old, new, kwargs) -> None:
        """ Charger stopped charging.
        """
        self.CleanUpWhenChargingStopped()


    def CleanUpWhenChargingStopped(self) -> None:
        """ Charger stopped charging.
        """
        if self.Car is not None:
            if self.Car.connectedCharger is self:
                if (
                    self.kWhRemaining() <= 2
                    or CHARGE_SCHEDULER.isPastChargingTime(vehicle_id = self.Car.vehicle_id)
                ):
                    self.Car.removeFromQueue()
                    if self.getChargingState() == 'Complete':
                        self.Car.turnOff_Charge_now()
                        if self.session_energy:
                            session = float(self.ADapi.get_state(self.session_energy, namespace = self.namespace))
                            if self.Car.maxkWhCharged < session:
                                self.Car.maxkWhCharged = session

                            # Calculations for battery size:
                            if (
                                self.Car.battery_sensor != None
                                and self.pct_start_charge < 90
                            ):
                                pctCharged = float(self.ADapi.get_state(self.Car.battery_sensor, namespace = self.namespace)) - self.pct_start_charge
                                if pctCharged > 35:
                                    if self.Car.battery_reg_counter == 0:
                                        avg = round((session / pctCharged)*100,2)
                                    else:
                                        avg = round(((self.Car.battery_size * self.Car.battery_reg_counter) + (session / pctCharged)*100) / self.Car.battery_reg_counter + 1,2)
                                    self.Car.battery_reg_counter += 1
                                    if self.Car.battery_reg_counter > 100:
                                        self.Car.battery_reg_counter = 10

                                    self.Car.battery_size = avg
                                    
                                elif pctCharged > 5 and self.Car.battery_size == 100:
                                    self.Car.battery_size = (session / pctCharged)*100

                self.pct_start_charge = 100
                self.ampereCharging = 0


    def setVoltPhase(self, volts, phases) -> None:
        """ Helper for calculations on chargespeed.
            VoltPhase is a make up name and simplification to calculate chargetime based on remaining kwh to charge
            230v 1 phase,
            266v is 3 phase on 230v without neutral (supported by tesla among others)
            687v is 3 phase on 400v with neutral.
        """
        if (
            phases > 1
            and volts > 200
            and volts < 250
        ):
            self.voltPhase = 266

        elif (
            phases == 3
            and volts > 300
        ):
            self.voltPhase = 687

        elif (
            phases == 1
            and volts > 200
            and volts < 250
        ):
            self.voltPhase = volts

        elif self.voltPhase == 0:
            self.voltPhase = 220


    def guestChargingListen(self, entity, attribute, old, new, kwargs) -> None:
        """ Disables logging and schedule if guest is using charger.
        """
        self.guestCharging = new == 'on'
        if (
            new == 'on'
            and old == 'off'
        ):
            self.startGuestCharging()
        elif (
            new == 'off'
            and old == 'on'
        ):
            if self.Car is not None:
                if self.Car.carName == 'guestCar':
                    self.stopCharging()
                    self.Car = None
                elif self.Car.getLocation() == 'home':
                    if (
                        self.Car.isConnected()
                        and self.kWhRemaining() > 0
                    ):
                        self.Car.findNewChargeTime()
            else:
                self.stopCharging()


    def idle_currentListen(self, entity, attribute, old, new, kwargs) -> None:
        """ Listens for changes to idle_current switch
        """
        if new == 'on':
            self.idle_current = True
        elif new == 'off':
            self.idle_current = False


    def startGuestCharging(self):
        """ Creates a guest car and starts charging.
        """
        guestCar = Car(api = self.ADapi,
            namespace = self.namespace,
            carName = 'guestCar',
            charger_sensor = self.charger_sensor,
            charge_limit = None,
            battery_sensor = None,
            asleep_sensor = None,
            online_sensor = None,
            location_tracker = None,
            software_update = None,
            force_data_update = None,
            polling_switch = None,
            data_last_update_time = None,
            battery_size = 100,
            pref_charge_limit = 100,
            priority = 1,
            finishByHour = 7,
            charge_now = True,
            charge_only_on_solar = False,
            departure = None
        )
        self.Car = guestCar
        self.Car.connectedCharger = self
        self.startCharging()


class Car:
    """ Car parent class
    Set variables in childclass before init:
        self.vehicle_id:str # Unik ID to separate chargers. CarName will be used if not set


    Set variables in childclass after init if needed:
        self.guestCharging:bool # Defaults to False
        self.Car # Car to charge
    """

    def __init__(self, api,
        namespace:str,
        carName:str, # Name of car
        charger_sensor:str, # Sensor chargecable connected
        charge_limit:str, # SOC limit sensor in %
        battery_sensor:str, # SOC in %
        asleep_sensor:str, # If car is sleeping
        online_sensor:str, # If car is online
        location_tracker:str, # Location of car
        software_update:str, # If cars updates software it probably can`t change charge speed or stop charging
        force_data_update:str, # Force Home Assistant Integration to pull new data
        polling_switch:str, # Turn off Home Assistant Integration pulling data from car
        data_last_update_time:str, # Last time Home Assistant pulled data
        battery_size:int, # Size of battery in kWh
        pref_charge_limit:int, # Preferred chargelimit in %
        priority:int, # Priority in chargecue
        finishByHour:str, # HA input_number for when car should be finished charging
        charge_now:str, # HA input_boolean to bypass smartcharging if true
        charge_only_on_solar:str, # HA input_boolean to charge only on solar
        departure:str # HA input_datetime for when to have car finished charging to 100%. Not implemented yet
    ):

        self.ADapi = api
        self.namespace = namespace

        if not hasattr(self, 'vehicle_id'):
            self.vehicle_id = carName
        self.carName = carName

        # Sensors
        self.car_charger_sensor = charger_sensor
        self.charge_limit = charge_limit
        self.battery_sensor = battery_sensor

        self.asleep_sensor = asleep_sensor
        self.online_sensor = online_sensor
        self.location_tracker = location_tracker
        self.software_update = software_update
        self.force_data_update = force_data_update
        self.polling_switch = polling_switch
        self.data_last_update_time = data_last_update_time

        # Car variables
        self.battery_size:float = battery_size
        self.battery_reg_counter = 0
        self.pref_charge_limit:int = pref_charge_limit

        self.priority:int = priority

        # Set up when car should be finished charging
        try:
            self.finishByHour = int(finishByHour)
        except ValueError:
            self.finishByHour = math.ceil(float(self.ADapi.get_state(finishByHour,
                namespace = self.namespace))
            )
            self.ADapi.listen_state(self.finishByHourListen, finishByHour,
                namespace = self.namespace
            )

        # Switch to start charging now
        if not charge_now:
            self.charge_now:bool = False
        else:
            self.charge_now_HA_switch:str = charge_now
            self.charge_now = self.ADapi.get_state(charge_now, namespace = self.namespace)  == 'on'
            self.ADapi.listen_state(self.chargeNowListen, charge_now,
                namespace = self.namespace
            )

        # Switch to charge only on solar
        if not charge_only_on_solar:
            self.charge_only_on_solar:bool = False
        else:
            self.charge_only_on_solar = self.ADapi.get_state(charge_only_on_solar, namespace = self.namespace)  == 'on'
            self.ADapi.listen_state(self.charge_only_on_solar_Listen, charge_only_on_solar,
                namespace = self.namespace
            )

        # Helper Variables:
        self.charging_on_solar:bool = False
        self.car_limit_max_charging:int = 0
        self.maxkWhCharged:float = 5 # Max kWh car has charged
        self.connectedCharger:object = None
        self.onboardCharger:object = None
        self.oldChargeLimit:int = 100


        # Check that car exists or get data from persistent json file
        with open(JSON_PATH, 'r') as json_read:
            ElectricityData = json.load(json_read)
        
        # NEW ENTRY IN PERSISTENT: 'car'
        if not 'car' in ElectricityData:
            ElectricityData.update(
                {'car' : {}}
            )
        
        if not self.vehicle_id in ElectricityData['car']:
            ElectricityData['car'].update(
                {self.vehicle_id : {
                    "CarLimitAmpere" : 0,
                    "MaxkWhCharged" : 5
                }}
            )
            with open(JSON_PATH, 'w') as json_write:
                json.dump(ElectricityData, json_write, indent = 4)
        else:
            if 'CarLimitAmpere' in ElectricityData['car'][self.vehicle_id]:
                self.car_limit_max_charging = math.ceil(float(ElectricityData['car'][self.vehicle_id]['CarLimitAmpere']))
            if 'MaxkWhCharged' in ElectricityData['car'][self.vehicle_id]:
                self.maxkWhCharged = float(ElectricityData['car'][self.vehicle_id]['MaxkWhCharged'])
            if 'batterysize' in ElectricityData['car'][self.vehicle_id]:
                self.battery_size = float(ElectricityData['car'][self.vehicle_id]['batterysize'])
                self.battery_reg_counter = int(ElectricityData['car'][self.vehicle_id]['Counter'])


        self.kWhRemainToCharge:float = self.kWhRemaining()

        # Set up listeners
        if self.car_charger_sensor:
            #self.ADapi.listen_state(self.car_ChargeCableConnected, self.car_charger_sensor,
            #    namespace = self.namespace,
            #    new = 'on'
            #)
            self.ADapi.listen_state(self.car_ChargeCableDisconnected, self.car_charger_sensor,
                namespace = self.namespace,
                new = 'off'
            )

        if self.charge_limit:
            self.ADapi.listen_state(self.ChargeLimitChanged, self.charge_limit,
                namespace = self.namespace
            )
            self.oldChargeLimit = self.ADapi.get_state(self.charge_limit,
                namespace = self.namespace
            )
        
        if (
            self.isConnected()
            and not self.hasChargingScheduled()
            and self.kWhRemaining() > 0
        ):
            self.ADapi.run_in(self.findNewChargeTimeAt, 120)

        """ TODO Departure / Maxrange handling: To be re-written before implementation
            Set a departure time in a HA datetime sensor for when car will be finished charging to 100%,
            to have a optimal battery when departing.
        """
        self.max_range_handler = None
        self.start_charging_max = None
        if departure != None:
            self.departure = departure

        """ Add Maxrange solution for charging finished to 100% at given time.
            #self.ADapi.listen_state(self.MaxRangeListener, self.departure, namespace = self.namespace, duration = 5 )
        """


        """ End initialization Car Class
        """

        # Functions on when to charge Car
    def finishByHourListen(self, entity, attribute, old, new, kwargs) -> None:
        """ Listener for HA input number for when car should be finished charging.
            Finds new time if changed.
        """
        self.finishByHour = math.ceil(float(new))
        if self.kWhRemaining() > 0:
            self.findNewChargeTime()


    def chargeNowListen(self, entity, attribute, old, new, kwargs) -> None:
        """ Listener for HA input boolean to disable smart charing and charge car now.
            Starts charing if turn on, finds new chargetime if turned off.
        """
        self.charge_now = new == 'on'
        if (
            new == 'on'
            and old == 'off'
            and self.connectedCharger is not None
        ):
            self.startCharging()
        elif (
            new == 'off'
            and old == 'on'
            and self.kWhRemaining() > 0
        ):
            self.findNewChargeTime()


    def turnOff_Charge_now(self) -> None:
        """ Turns smart charging on again.
        """
        if self.charge_now:
            self.ADapi.call_service('input_boolean/turn_off',
                entity_id = self.charge_now_HA_switch,
                namespace = self.namespace,
            )


    def charge_only_on_solar_Listen(self, entity, attribute, old, new, kwargs) -> None:
        """ Listener for HA input boolean to enable/disable solar charing.
        """
        self.charge_only_on_solar = new == 'on'
        if new == 'on':
            self.removeFromQueue()
            self.turnOff_Charge_now()
        elif new == 'off':
            if self.kWhRemaining() > 0:
                self.findNewChargeTime()


        # Functions for charge times
    def findNewChargeTimeAt(self, kwargs) -> None:
        """ Function to run when initialized and when new prices arrive.
        """
        if (
            self.getLocation() == 'home'
            and self.kWhRemaining() > 0
            and self.isConnected()
            and self.getCarChargerState() != 'Complete'
        ):
            self.findNewChargeTime()


    def findNewChargeTimeWhen(self, entity, attribute, old, new, kwargs) -> None:
        """ Find new chargetime when car is registered as home, if car was not home when connected.
        """
        self.findNewChargeTime()


    def findNewChargeTime(self) -> None:
        """ Find new chargetime for car.
        """
        startcharge = False
        charger_state = self.getCarChargerState()

        if (
            self.connectedCharger is None
            and self.getLocation() == 'home'
            and charger_state != 'NoPower'
            and charger_state != 'Disconnected'
        ):
            self.connectedCharger = self.onboardCharger

        if self.getLocation() == 'home':
            if (
                charger_state != 'Disconnected'
                and charger_state != 'Complete'
                and not self.charging_on_solar
                and not self.charge_only_on_solar
            ):
                if CHARGE_SCHEDULER.informHandler != None:
                    if self.ADapi.timer_running(CHARGE_SCHEDULER.informHandler):
                        try:
                            self.ADapi.cancel_timer(CHARGE_SCHEDULER.informHandler)
                        except Exception as e:
                            self.ADapi.log(
                                f"Not possible to stop timer to run sum and inform chargetime. Exception: {e}",
                                level = 'DEBUG'
                            )

                CHARGE_SCHEDULER.informHandler = self.ADapi.run_in(CHARGE_SCHEDULER.notifyChargeTime, 3)

                startcharge = CHARGE_SCHEDULER.queueForCharging(
                    vehicle_id = self.vehicle_id,
                    kWhRemaining = self.kWhRemainToCharge,
                    maxAmps = self.connectedCharger.getmaxChargingAmps(),
                    voltPhase = self.connectedCharger.voltPhase,
                    finishByHour = self.finishByHour,
                    priority = self.priority,
                    name = self.carName
                )

                if (
                    charger_state == 'Charging'
                    and not startcharge
                ):
                    if self.hasChargingScheduled():
                        start, stop = CHARGE_SCHEDULER.getCharingTime(vehicle_id = self.vehicle_id)
                        if start:
                            if start - datetime.timedelta(minutes=12) > datetime.datetime.now():
                                self.stopCharging()
                    else:
                        self.stopCharging()

        elif self.getLocation() != 'home':
            self.ADapi.listen_state(self.findNewChargeTimeWhen, self.location_tracker,
                namespace = self.namespace,
                new = 'home'
            )


    def removeFromQueue(self) -> None:
        """ Removes car from chargequeue
        """
        CHARGE_SCHEDULER.removeFromQueue(vehicle_id = self.vehicle_id)


    def hasChargingScheduled(self) -> bool:
        """ returns if car has charging scheduled
        """
        return CHARGE_SCHEDULER.hasChargingScheduled(vehicle_id = self.vehicle_id)


        # Functions to react to car sensors
    def car_ChargeCableConnected(self, entity, attribute, old, new, kwargs) -> None:
        """ Charge cable connected for car.
        """
        pass


    def car_ChargeCableDisconnected(self, entity, attribute, old, new, kwargs) -> None:
        """ Charge cable disconnected for car.
        """
        if self.connectedCharger is not None:
            if self.connectedCharger.getChargingState() == 'Disconnected':
                self.removeFromQueue()
                self.turnOff_Charge_now()

                if self.connectedCharger.Car.onboardCharger is not self.connectedCharger:
                    self.connectedCharger.Car = None
                self.connectedCharger = None


            if self.max_range_handler != None:
                # TODO: Program charging to max at departure time.
                # @HERE: Call a function that will cancel handler when car is disconnected
                #self.ADapi.run_in(self.resetMaxRangeCharging, 1)
                self.ADapi.log(f"{self.charger} Has a max_range_handler. Not Programmed yet", level = 'DEBUG')


    def isConnected(self) -> bool:
        """ Returns True if charge cable is connected.
        """
        if self.getLocation() == 'home':
            if self.car_charger_sensor != None:
                return self.ADapi.get_state(self.car_charger_sensor, namespace = self.namespace) == 'on'
            return True
        return False


    def asleep(self) -> bool:
        """ Returns True if car is sleeping.
        """
        if self.asleep_sensor:
            return self.ADapi.get_state(self.asleep_sensor, namespace = self.namespace) == 'on'
        return False


    def wakeMeUp(self) -> None:
        """ Function to wake up connected cars.
        """
        pass


    def isOnline(self) -> bool:
        """ Returns True if car in online.
        """
        if self.online_sensor:
            return self.ADapi.get_state(self.online_sensor, namespace = self.namespace) == 'on'
        return True


    def getLocation(self) -> str:
        """ Returns location of the vehicle based on sones from Home Assistant.
        """
        if self.location_tracker:
            return self.ADapi.get_state(self.location_tracker, namespace = self.namespace)
        return 'home'


    def SoftwareUpdates(self) -> bool:
        """ Return True if car is updating software.
        """
        return False


    def forceAPIupdate(self) -> None:
        """ Function to force a new API pull on the vehicle.
        """
        pass


    def polling_of_data(self) -> bool:
        """ Polling of data is a switch that disables communication with the car when switched off.
            TODO: Implement checks to not control/wake car if this is off.
        """
        if self.polling_switch:
            return self.ADapi.get_state(self.polling_switch, namespace = self.namespace) == 'on'
        return True


    def recentlyUpdated(self) -> bool:
        """ Returns True if car data is updated within the last 12 minutes.
        """
        if self.data_last_update_time:
            last_update = self.ADapi.convert_utc(self.ADapi.get_state(self.data_last_update_time,
                namespace = self.namespace)
            )
            
            now: datetime = self.ADapi.datetime(aware=True)
            stale_time = now - last_update
            if stale_time < datetime.timedelta(minutes = 12):
                return False
        return True


    def dontStopMeNow(self) -> bool:
        """ Returns true if charger should not or can not be stopped.
        """
        if (
            self.charge_now
            or self.charging_on_solar
            or self.SoftwareUpdates()
        ):
            return True
        return False


    def kWhRemaining(self) -> float:
        """ Calculates kWh remaining to charge car from battery sensor/size and charge limit.
        """
        if self.charge_limit:
            battery_pct = self.car_battery_soc()
            limit_pct = self.ADapi.get_state(self.charge_limit, namespace = self.namespace)

            try:
                battery_pct = float(battery_pct)
                limit_pct = float(limit_pct)
            except (ValueError, TypeError) as ve:
                try:
                    kWhRemain = float(self.kWhRemainToCharge)
                except Exception:
                    kWhRemain = -1
                    self.kWhRemainToCharge = -1
                    if (
                        self.getLocation() == 'home'
                        or self.getLocation() == 'unknown'
                    ):
                        self.wakeMeUp() # Wake up car to get proper value.
                else:
                    self.ADapi.log(
                        f"Not able to calculate kWh Remaining To Charge based on battery soc: {battery_pct} and limit: {limit_pct} for {self.carName}. "
                        f"Return existing value: {self.kWhRemainToCharge}. ValueError: {ve}",
                        level = 'DEBUG'
                    )
                return self.kWhRemainToCharge


            except Exception as e:
                self.ADapi.log(
                    f"Not able to calculate kWh Remaining To Charge based on battery soc: {battery_pct} and limit: {limit_pct} for {self.carName}. "
                    f"Return existing value: {self.kWhRemainToCharge}. Exception: {e}",
                    level = 'WARNING'
                )
                return self.kWhRemainToCharge

            if battery_pct < limit_pct:
                percentRemainToCharge = limit_pct - battery_pct
                self.kWhRemainToCharge = (percentRemainToCharge / 100) * self.battery_size
            else:
                self.kWhRemainToCharge = -1
            return self.kWhRemainToCharge

        else:
            # Calculate remaining to charge based on max kWh Charged and session energy in charger class
            return -2


    def car_battery_soc(self) -> int:
        """ Returns battery State of charge.
        """
        SOC = -1
        if self.battery_sensor:
            try:
                SOC = float(self.ADapi.get_state(self.battery_sensor, namespace = self.namespace))
            except (ValueError, TypeError) as ve:
                self.ADapi.log(
                    f"{self.carName} Not able to get SOC. Trying alternative calculations. ValueError: {ve}",
                    level = 'DEBUG'
                )
            except Exception as e:
                self.ADapi.log(
                    f"{self.carName} Not able to get SOC. Trying alternative calculations. Exception: {e}",
                    level = 'WARNING'
                )
        if SOC == -1:
            try:
                kWhRemain = float(self.kWhRemainToCharge)
            except Exception:
                kWhRemain = -1
            if kWhRemain == -1:
                SOC = 100
            else: # TODO: Find a way to calculate
                SOC = 10

        return SOC


    def changeChargeLimit(self, chargeLimit:int = 100 ) -> None:
        """ Change charge limit.
        """
        self.oldChargeLimit = self.ADapi.get_state(self.charge_limit, namespace = self.namespace)
        self.ADapi.call_service('number/set_value',
            value = chargeLimit,
            entity_id = self.charge_limit,
            namespace = self.namespace
        )


    def ChargeLimitChanged(self, entity, attribute, old, new, kwargs) -> None:
        """ Charge limit changed.
        """
        if self.getLocation() == 'home':
            try:
                new = int(new)
                self.oldChargeLimit = int(old)
            except (ValueError, TypeError) as ve:
                self.ADapi.log(
                    f"{self.carName} new charge limit: {new}. Error: {ve}",
                    level = 'DEBUG'
                )
                return
            except Exception as e:
                self.ADapi.log(
                    f"Not able to process {self.charger} new charge limit: {new}. Exception: {e}",
                    level = 'DEBUG'
                )
                return

            try:
                battery_state = float(self.ADapi.get_state(self.battery_sensor,
                    namespace = self.namespace)
                )
            except (ValueError, TypeError) as ve:
                self.ADapi.log(
                    f"{self.charger} battery state error {battery_state} when setting new charge limit: {new}. Error: {ve}",
                    level = 'DEBUG'
                )
                return
            if battery_state > float(new):
                self.removeFromQueue()
                self.turnOff_Charge_now()
                self.kWhRemainToCharge = -1

            elif self.kWhRemaining() > 0:
                self.findNewChargeTime()


    def getCarChargerState(self) -> str:
        """ Returns the charging state of the car.
            Valid returns: 'Complete' / 'None' / 'Stopped' / 'Charging' / 'Disconnected' / 'Starting' / 'NoPower'.
        """
        if self.connectedCharger is not None:
            return self.connectedCharger.getChargingState()

        return 'Disconnected'


    def startCharging(self) -> None:
        """ Starts controlling charger.
        """
        if (
            self.getCarChargerState() == 'Stopped'
            or self.connectedCharger.getChargingState() == 'awaiting_start'
        ):
            self.connectedCharger.startCharging()
        elif self.getCarChargerState() == 'Complete':
            self.removeFromQueue()


    def stopCharging(self) -> None:
        """ Stops controlling charger.
        """
        self.connectedCharger.stopCharging()


class Tesla_charger(Charger):
    """ Tesla
        Child class of Charger. Uses Tesla custom integration. https://github.com/alandtse/tesla Easiest installation is via HACS.
    
        Selection of possible commands to API
            self.ADapi.call_service('tesla_custom/api', command = 'STOP_CHARGE', parameters = { 'path_vars': {'vehicle_id': self.charger_id}, 'wake_if_asleep': True} )
            self.ADapi.call_service('tesla_custom/api', command = 'CHANGE_CHARGE_LIMIT', parameters = { 'path_vars': {'vehicle_id': self.charger_id}, 'percent': '70'} )
            self.ADapi.call_service('tesla_custom/api', command = 'CHANGE_CHARGE_MAX', parameters = { 'path_vars': {'vehicle_id': self.charger_id}} )  #?
            self.ADapi.call_service('tesla_custom/api', command = 'CHARGING_AMPS', parameters = { 'path_vars': {'vehicle_id': self.charger_id}, 'charging_amps': '25'} )

    """

    def __init__(self, api,
        Car,
        namespace:str,
        charger:str, # Name of your Tesla
        charger_sensor:str, # Binary_sensor with attributes with status
        charger_switch:str, # Charging or not
        charging_amps:str, # Ampere charging
        charger_power:str, # Charger power. Contains volts and phases
        session_energy:str # Charged this session in kWh
    ):

        self.charger_id = api.get_state(Car.online_sensor,
            namespace = Car.namespace,
            attribute = 'id'
        )
        self.volts:int = 220
        self.phases:int = 1

        if Car.getLocation() == 'home':

            try:
                self.volts = math.ceil(float(api.get_state(charger_power,
                namespace = namespace,
                attribute = 'charger_volts'))
            )
            except (ValueError, TypeError):
                self.volts = 220
            except Exception as e:
                api.log(
                    f"Error trying to get voltage: "
                    f"{api.get_state(charger_power, namespace = namespace, attribute = 'charger_volts')}. "
                    f"Exception: {e}", level = 'WARNING'
                )

            try:
                self.phases = int(api.get_state(charger_power,
                namespace = namespace,
                attribute = 'charger_phases')
            )
            except (ValueError, TypeError):
                self.phases = 1
            except Exception as e:
                api.log(f"Error trying to get phases: "
                    f"{(api.get_state(charger_power, namespace = namespace, attribute = 'charger_phases'))}. "
                    f"Exception: {e}", level = 'WARNING'
                )

        # Onboard charger can only be linked to one car.
        self.cars:list = [Car]

        super().__init__(
            api = api,
            namespace = namespace,
            charger = charger,
            charger_sensor = charger_sensor,
            charger_switch = charger_switch,
            charging_amps = charging_amps,
            charger_power = charger_power,
            session_energy = session_energy
        )

        self.Car = Car
        self.Car.onboardCharger = self

        self.min_ampere = 5
        self.noPowerDetected_handler = None

        self.ADapi.listen_state(self.ChargingStarted, self.charger_switch,
            namespace = self.namespace,
            new = 'on'
        )
        self.ADapi.listen_state(self.ChargingStopped, self.charger_switch,
            namespace = self.namespace,
            new = 'off'
        )
        self.ADapi.listen_state(self.ChargingConnected, self.charger_sensor,
            namespace = self.namespace
        )

        self.ADapi.listen_state(self.MaxAmpereChanged, self.charging_amps,
            namespace = self.namespace,
            attribute = 'max'
        )
        """ End initialization Tesla Charger Class
        """


    def getChargingState(self) -> str:
        """ Returns the charging state of the charger.
            Valid returns: 'Complete' / 'None' / 'Stopped' / 'Charging' / 'Disconnected' / 'Starting' / 'NoPower'.
        """
        try:
            state = self.ADapi.get_state(self.charger_sensor,
                namespace = self.namespace,
                attribute = 'charging_state'
            )
            if state == 'Starting':
                state = 'Charging'
        except (ValueError, TypeError) as ve:
            return None
        except Exception as e:
            self.ADapi.log(
                f"{self.charger} Could not get attribute = 'charging_state' from: "
                f"{self.ADapi.get_state(self.charger_sensor, namespace = self.namespace)} "
                f"Exception: {e}",
                level = 'WARNING'
            )
            return None

        # Set as connected charger if restarted after cable connected.
        if (
            state == 'Stopped'
            and self.Car.connectedCharger is None
        ):
            self.Car.connectedCharger = self

        return state


    def setmaxChargingAmps(self) -> None:
        """ Set maxChargerAmpere from charger sensors
        """
        if self.Car is not None:
            if (
                self.Car.getLocation() == 'home'
                and self.getChargingState() != 'NoPower'
                and self.getChargingState() != 'Disconnected'
                and self.getChargingState() != 'Complete'
            ):
                if self.ADapi.get_state(self.charging_amps, namespace = self.namespace) != 'unavailable':
                    try:
                        self.maxChargerAmpere = math.ceil(float(self.ADapi.get_state(self.charging_amps,
                            namespace = self.namespace,
                            attribute = 'max'))
                        )
                    except (ValueError, TypeError) as ve:
                        self.ADapi.log(
                            f"{self.charger} Could not get maxChargingAmps. ValueError: {ve}",
                            level = 'DEBUG'
                        )
                        return
                    except Exception as e:
                        self.ADapi.log(
                            f"{self.charger} Could not get maxChargingAmps. Exception: {e}",
                            level = 'WARNING'
                        )
                        return

                    # Update Voltphase calculations
                    try:
                        self.volts = math.ceil(float(self.ADapi.get_state(self.charger_power,
                            namespace = self.namespace,
                            attribute = 'charger_volts'
                        )))
                    except (ValueError, TypeError):
                        pass
                    except Exception as e:
                        self.ADapi.log(
                            f"Error trying to get voltage: "
                            f"{self.ADapi.get_state(self.charger_power, namespace = self.namespace, attribute = 'charger_volts')}. "
                            f"Exception: {e}", level = 'WARNING'
                        )

                    try:
                        self.phases = int(self.ADapi.get_state(self.charger_power,
                            namespace = self.namespace,
                            attribute = 'charger_phases'
                        ))
                    except (ValueError, TypeError):
                        pass
                    except Exception as e:
                        self.ADapi.log(f"Error trying to get phases: "
                            f"{(self.ADapi.get_state(self.charger_power, namespace = self.namespace, attribute = 'charger_phases'))}. "
                            f"Exception: {e}", level = 'WARNING'
                        )

                    self.setVoltPhase(
                        volts = self.volts,
                        phases = self.phases
                    )


    def getmaxChargingAmps(self) -> int:
        """ Returns the maximum ampere the car/charger can get/deliver.
        """
        return self.maxChargerAmpere



    def setChargingAmps(self, charging_amp_set:int = 16) -> int:
        """ Function to set ampere charging to received value.
            returns actual restricted within min/max ampere.
        """
        self.ampereCharging = super().setChargingAmps(charging_amp_set = charging_amp_set)
        self.ADapi.call_service('tesla_custom/api',
            namespace = self.namespace,
            command = 'CHARGING_AMPS',
            parameters = {'path_vars': {'vehicle_id': self.charger_id}, 'charging_amps': self.ampereCharging}
        )


    def MaxAmpereChanged(self, entity, attribute, old, new, kwargs) -> None:
        """ Detects if smart charger (Easee) increases ampere available to charge and updates internal charger to follow.
        """
        if float(new) > self.ampereCharging:
            self.setChargingAmps(charging_amp_set = self.maxChargerAmpere)
            

    def ChargingConnected(self, entity, attribute, old, new, kwargs) -> None:
        """ Function that reacts to charger_sensor connected or disconnected.
        """
        if self.Car is not None:
            if (
                self.Car.connectedCharger is None
                or self.Car.connectedCharger is self
            ):

                if self.noPowerDetected_handler != None:
                    try:
                        self.ADapi.cancel_listen_state(self.noPowerDetected_handler)
                    except Exception as exc:
                        self.ADapi.log(
                            f"Could not stop hander listening for NoPower {self.noPowerDetected_handler}. Exception: {exc}",
                            level = 'DEBUG'
                        )
                    self.noPowerDetected_handler = None

                if (
                    new == 'on'
                    and self.getChargingState() != 'NoPower'
                    and self.Car.getLocation() == 'home'
                    and self.kWhRemaining() > 0
                ):
                    self.setmaxChargingAmps()
                    if self.Car.connectedCharger is None:
                        if not self.findCarConnectedToCharger():
                            return
                    # Listen for changes made from other connected chargers
                    self.noPowerDetected_handler = self.ADapi.listen_state(self.noPowerDetected, self.charger_sensor,
                        namespace = self.namespace,
                        attribute = 'charging_state',
                        new = 'NoPower'
                    )

                    # Find chargetime
                    if self.ADapi.get_state(self.charger_switch, namespace = self.namespace) == 'on':
                        return # Calculations will be handeled by ChargingStarted

                    self.Car.findNewChargeTime()

                elif (
                    new == 'on'
                    and self.getChargingState() == 'NoPower'
                    and self.Car.getLocation() == 'home'
                    and self.kWhRemaining() > 0
                ):
                    self.setChargingAmps(charging_amp_set = self.maxChargerAmpere)


    def noPowerDetected(self, entity, attribute, old, new, kwargs) -> None:
        """ Reacts when chargecable is connected but no power is given.
            This indicates that a smart connected charger has cut the power.
        """
        if self.Car.connectedCharger is self:
            self.Car.connectedCharger = None


    def ChargingStopped(self, entity, attribute, old, new, kwargs) -> None:
        """ Charger stopped charging.
        """
        self.CleanUpWhenChargingStopped()
        self.setChargingAmps(charging_amp_set = self.min_ampere) # Set to minimum amp for preheat.


    def startCharging(self) -> None:
        """ Starts charger.
        """
        if super().startCharging():
            try:
                self.ADapi.call_service('tesla_custom/api',
                    namespace = self.namespace,
                    command = 'START_CHARGE',
                    parameters = { 'path_vars': {'vehicle_id': self.charger_id}, 'wake_if_asleep': True}
                )
                if self.Car is not None:
                    self.Car.forceAPIupdate()
            except Exception as e:
                self.ADapi.log(f"{self.charger} Could not Start Charging. Exception: {e}", level = 'WARNING')


    def stopCharging(self) -> None:
        """ Stops charger.
        """
        if super().stopCharging():
            try:
                self.ADapi.call_service('tesla_custom/api',
                    namespace = self.namespace,
                    command = 'STOP_CHARGE',
                    parameters = { 'path_vars': {'vehicle_id': self.charger_id}, 'wake_if_asleep': True}
                )
                if self.Car is not None:
                    self.Car.forceAPIupdate()
            except Exception as e:
                self.ADapi.log(f"{self.charger} Could not Stop Charging: {e}", level = 'WARNING')


    def checkIfChargingStarted(self, kwargs) -> None:
        """ Check if charger was able to start.
        """
        if (
            self.getChargingState() == 'NoPower'
            and self.Car.connectedCharger is self
        ):
            self.Car.connectedCharger = None

        elif not super().checkIfChargingStarted(0):
            if self.Car is not None:
                self.Car.forceAPIupdate()
            try:
                self.ADapi.call_service('tesla_custom/api',
                    namespace = self.namespace,
                    command = 'START_CHARGE',
                    parameters = { 'path_vars': {'vehicle_id': self.charger_id}, 'wake_if_asleep': True}
                )
            except Exception as e:
                self.ADapi.log(
                    f"Could not Start Charging in checkIfChargingStarted for {self.charger}. Exception: {e}",
                    level = 'DEBUG'
                )


    def checkIfChargingStopped(self, kwargs) -> None:
        """ Check if charger was able to stop.
        """
        if not super().checkIfChargingStopped(0):
            if self.Car is not None:
                self.Car.forceAPIupdate()
            try:
                self.ADapi.call_service('tesla_custom/api',
                    namespace = self.namespace,
                    command = 'STOP_CHARGE',
                    parameters = { 'path_vars': {'vehicle_id': self.charger_id}, 'wake_if_asleep': True}
                )
            except Exception as e:
                self.ADapi.log(
                    f"Could not Stop Charging in checkIfChargingStopped for {self.charger}. Exception: {e}",
                    level = 'DEBUG'
                )


class Tesla_car(Car):

    def __init__(self, api,
        namespace,
        carName, # Unique name of charger/car
        charger_sensor, # Sensor chargecable connected
        charge_limit, # SOC limit sensor
        battery_sensor, # SOC (State Of Charge)
        asleep_sensor, # If car is sleeping
        online_sensor, # If car is online
        location_tracker, # Location of car/charger
        destination_location_tracker, # Destination of car
        arrival_time, # Sensor with Arrival time, estimated energy at arrival and destination.
        software_update, # If Tesla updates software it can`t change or stop charging
        force_data_update, # Button to force car to send update to HA
        polling_switch,
        data_last_update_time,
        battery_size:int, # User input size of battery. Used to calculate amount of time to charge
        pref_charge_limit:int, # User input if prefered SOC limit is other than 90%
        priority:int, # Priority. See full description in Readme
        finishByHour:str, # HA input_number for when car should be finished charging
        charge_now:str, # HA input_boolean to bypass smartcharging if true
        charge_only_on_solar:str, # HA input_boolean to charge only on solar
        departure:str # HA input_datetime for when to have car finished charging to 100%. Not implemented yet
    ):

        self.vehicle_id = api.get_state(online_sensor,
            namespace = namespace,
            attribute = 'id'
        )

        super().__init__(
            api = api,
            namespace = namespace,
            carName = carName,
            charger_sensor = charger_sensor,
            charge_limit = charge_limit,
            battery_sensor = battery_sensor,
            asleep_sensor = asleep_sensor,
            online_sensor = online_sensor,
            location_tracker = location_tracker,
            software_update = software_update,
            force_data_update = force_data_update,
            polling_switch = polling_switch,
            data_last_update_time = data_last_update_time,
            battery_size = battery_size,
            pref_charge_limit = pref_charge_limit,
            priority = priority,
            finishByHour = finishByHour,
            charge_now = charge_now,
            charge_only_on_solar = charge_only_on_solar,
            departure = departure
        )

        """ End initialization Tesla Car Class
        """


    def getCarChargerState(self) -> str:
        """ Returns the charging state of the car.
            Valid returns: 'Complete' / 'None' / 'Stopped' / 'Charging' / 'Disconnected' / 'Starting' / 'NoPower'.
        """
        try:
            state = self.ADapi.get_state(self.car_charger_sensor,
                namespace = self.namespace,
                attribute = 'charging_state'
            )
        except (ValueError, TypeError) as ve:
            self.ADapi.log(
                f"{self.charger} Could not get attribute = 'charging_state' from: "
                f"{self.ADapi.get_state(self.car_charger_sensor, namespace = self.namespace)} "
                f"Error: {ve}",
                level = 'DEBUG'
            )
            return None

        except Exception as e:
            self.ADapi.log(
                f"{self.charger} Could not get attribute = 'charging_state' from: "
                f"{self.ADapi.get_state(self.car_charger_sensor, namespace = self.namespace)} "
                f"Exception: {e}",
                level = 'WARNING'
            )
            return None

        if state == 'Starting':
            state = 'Charging'

        return state


    def wakeMeUp(self) -> None:
        """ Function to wake up connected cars.
        """
        if self.ADapi.get_state(self.polling_switch, namespace = self.namespace) == 'on':
            if (
                self.ADapi.get_state(self.car_charger_sensor, namespace = self.namespace) != 'Complete'
                and self.ADapi.get_state(self.car_charger_sensor, namespace = self.namespace) != 'Disconnected'
            ):
                if (
                    not self.recentlyUpdated()
                    and self.asleep()
                ):
                    self.ADapi.call_service('tesla_custom/api',
                        namespace = self.namespace,
                        command = 'WAKE_UP',
                        parameters = { 'path_vars': {'vehicle_id': self.vehicle_id}, 'wake_if_asleep' : True}
                    )
                self.forceAPIupdate()


    def SoftwareUpdates(self) -> bool:
        """ Return True if car is updating software.
        """
        if (
            self.ADapi.get_state(self.software_update, namespace = self.namespace) != 'unknown'
            and self.ADapi.get_state(self.software_update, namespace = self.namespace) != 'unavailable'
        ):
            if self.ADapi.get_state(self.software_update, namespace = self.namespace, attribute = 'in_progress') != False:
                return True
        return False


    def forceAPIupdate(self) -> None:
        """ Function to force a new API pull on the vehicle.
        """
        self.ADapi.call_service('button/press',
            namespace = self.namespace,
            entity_id = self.force_data_update
        )


    def changeChargeLimit(self, chargeLimit:int = 90 ) -> None:
        """ Change charge limit.
        """
        self.oldChargeLimit = self.ADapi.get_state(self.charge_limit, namespace = self.namespace)
        self.ADapi.call_service('number/set_value',
            value = chargeLimit,
            entity_id = self.charge_limit,
            namespace = self.namespace
        )


class Easee(Charger):
    """ Easee
        Child class of Charger. Uses Easee EV charger component for Home Assistant. https://github.com/nordicopen/easee_hass 
        Easiest installation is via HACS.
    """

    def __init__(self, api,
        cars:list,
        namespace:str,
        charger:str, # Name of your Easee
        charger_sensor:str, # sensor.charger_status
        reason_for_no_current:str, # No charger_switch in Easee integration
        charging_amps:str, # Sensor with current
        charger_power:str, # Charger power in kW
        session_energy:str, # Charged this session. In kWh
        voltage:str, # Voltage sensor
        max_charger_limit:str, # Max available ampere in charger
        idle_current:str, # Allow Idle current for preheating
        guest:str # HA input_boolean for when a guest car borrows charger.
    ):

        self.charger_id:str = api.get_state(charger_sensor,
            namespace = namespace,
            attribute = 'id'
        )

        self.voltage = voltage
        try:
            self.volts = math.ceil(float(api.get_state(voltage,
                namespace = namespace))
            )
        except ValueError:
            self.volts = 220
        except Exception as e:
            api.log(f"Error trying to get voltage for {charger} from {voltage}. Exception: {e}",
            level = 'WARNING'
        )

        try:
            self.phases = int(api.get_state(charger_sensor,
            namespace = namespace,
            attribute = 'config_phaseMode')
        )
        except (ValueError, TypeError):
            self.phases = 1
        except Exception as e:
            api.log(f"Error trying to get phases for {charger} from {charger_sensor}. Exception: {e}",
                level = 'WARNING'
            )

        self.max_charger_limit = max_charger_limit

        self.cars:list = cars

        super().__init__(
            api = api,
            namespace = namespace,
            charger = charger,
            charger_sensor = charger_sensor,
            charger_switch = None,
            charging_amps = charging_amps,
            charger_power = charger_power,
            session_energy = session_energy
        )

        # Minumum ampere if locked to 3 phase
        if self.phases == 3:
            self.min_ampere = 11

        # Switch to allow guest to charge
        if not guest:
            self.guestCharging = False
        else:
            self.guestCharging = api.get_state(guest, namespace = namespace) == 'on'
            api.listen_state(self.guestChargingListen, guest,
                namespace = namespace
            )

        # Switch to allow current when preheating
        if not idle_current:
            self.idle_current = False
        else:
            self.idle_current = api.get_state(idle_current, namespace = namespace) == 'on'
            api.listen_state(self.idle_currentListen, idle_current,
                namespace = namespace
            )

        api.listen_state(self.statusChange, charger_sensor, namespace = namespace)

        if (
            api.get_state(self.charger_sensor, namespace = self.namespace) != 'disconnected'
            and self.Car is None
        ):
            self.findCarConnectedToCharger()


        elif type(Car).__name__ == 'Car':
            api.listen_state(self.reasonChange, reason_for_no_current, namespace = namespace)

        """ End initialization Easee Charger Class
        """


    def compareChargingState(self, car_status:str) -> bool:
        """ Returns True if car and charger match charging state.
        """
        charger_status = self.ADapi.get_state(self.charger_sensor, namespace = self.namespace)
        if charger_status == 'charging':
            return car_status == 'Charging'
        elif charger_status == 'completed':
            return car_status == 'Complete'
        elif charger_status == 'awaiting_start':
            return car_status == 'NoPower'
        elif charger_status == 'disconnected':
            return car_status == 'Disconnected'

        return False


    def getChargingState(self) -> str:
        """ Returns the charging state of the charger.
            Easee state can be: 'awaiting_start' / 'charging' / 'completed' / 'disconnected' / from charger_status
            Valid returns: 'Complete' / 'None' / 'Stopped' / 'Charging' / 'Disconnected' / 'Starting' / 'NoPower'.
        """
        status = self.ADapi.get_state(self.charger_sensor, namespace = self.namespace)
        if status == 'charging':
            return 'Charging'
        elif status == 'completed':
            return 'Complete'
        elif status == 'awaiting_start':
            return 'awaiting_start'
        elif status == 'disconnected':
            return 'Disconnected'
        elif not status == 'ready_to_charge':
            self.ADapi.log(f"Status: {status} for {self.charger} is not defined", level = 'WARNING')
        return status


    def statusChange(self, entity, attribute, old, new, kwargs) -> None:
        """ Listens to changes in state of the charger.
            Easee state can be: 'awaiting_start' / 'charging' / 'completed' / 'disconnected' / from charger_status
        """
        self.setPhases()

        if old == 'disconnected':
            if self.findCarConnectedToCharger():
                if self.Car is not None:
                    self.kWhRemaining() # Update kWh remaining to charge
                    self.Car.findNewChargeTime()
            else:
                self.stopCharging()
                return

        elif (
            new != 'disconnected'
            and old == 'completed'
        ):
            if self.Car is not None:
                if (
                    self.kWhRemaining() > 2
                    and CHARGE_SCHEDULER.isPastChargingTime(vehicle_id = self.Car.vehicle_id)
                ):
                    self.Car.findNewChargeTime()

                if (
                    CHARGE_SCHEDULER.isChargingTime(vehicle_id = self.Car.vehicle_id)
                    or self.idle_current
                ):
                    # Preheating
                    return

            self.stopCharging()

        elif (
            new == 'charging'
            or new == 'ready_to_charge'
        ):
            if self.Car is None:
                if not self.findCarConnectedToCharger():
                    self.stopCharging()
                    return

            if self.Car is not None:
                if (
                    not CHARGE_SCHEDULER.hasChargingScheduled(vehicle_id = self.Car.vehicle_id)
                    or CHARGE_SCHEDULER.isPastChargingTime(vehicle_id = self.Car.vehicle_id)
                ):
                    self.kWhRemaining() # Update kWh remaining to charge
                    self.Car.findNewChargeTime()

                elif not CHARGE_SCHEDULER.isChargingTime(vehicle_id = self.Car.vehicle_id):
                    self.stopCharging()
            else:
                self.stopCharging()
                return

        elif (
            new == 'completed'
            or new == 'disconnected'
        ):
            if self.Car is not None:
                self.CleanUpWhenChargingStopped()

                if new == 'disconnected':
                    self.Car.connectedCharger = None
                    self.Car = None
        elif new == 'awaiting_start':
            self.CleanUpWhenChargingStopped()
            if self.Car is None:
                self.findCarConnectedToCharger()


    def reasonChange(self, entity, attribute, old, new, kwargs) -> None:
        """ Listens to reasonChange in Easee charger.
            Easee reason can be:
            'no_current_request' / 'undefined' / 'waiting_in_queue' / 'limited_by_charger_max_limit' /
            'limited_by_local_adjustment' / 'limited_by_car' / 'car_not_charging' /  from reason_for_no_current
        """
        if (
            new == 'limited_by_car'
        ):
            chargingAmpere = math.ceil(float(self.ADapi.get_state(self.charging_amps,
                namespace = self.namespace))
            )
            if (
                self.Car.car_limit_max_charging != chargingAmpere
                and chargingAmpere >= 6
            ):
                self.Car.car_limit_max_charging = chargingAmpere


    def setmaxChargingAmps(self) -> None:
        """ Set maxChargerAmpere from charger sensors
        """
        try:
            self.maxChargerAmpere = math.ceil(float(self.ADapi.get_state(self.max_charger_limit,
                namespace = self.namespace))
            )
        except Exception:
            self.maxChargerAmpere = 32
        
        self.setVolts()
        self.setPhases()

        self.setVoltPhase(
            volts = self.volts,
            phases = self.phases
        )

    def setVolts(self):
        try:
            self.volts = math.ceil(float(self.ADapi.get_state(self.voltage,
                namespace = self.namespace))
            )
        except ValueError:
            return
        except Exception as e:
            self.ADapi.log(
                f"Error trying to get voltage for {self.charger} from {self.volts}. Exception: {e}",
                level = 'WARNING'
            )
            return

    def setPhases(self):
        try:
            self.phases = int(self.ADapi.get_state(self.charger_sensor,
            namespace = self.namespace,
            attribute = 'config_phaseMode')
        )
        except (ValueError, TypeError):
            self.phases = 1
        except Exception as e:
            self.ADapi.log(
                f"Error trying to get phases for {self.charger} from {self.charger_sensor}. Exception: {e}",
                level = 'WARNING'
            )


    def setChargingAmps(self, charging_amp_set:int = 16) -> None:
        """ Function to set ampere charging to received value.
            returns actual restricted within min/max ampere.
        """
        charging_amp_set = super().setChargingAmps(charging_amp_set = charging_amp_set)
        if (
            self.ampereCharging != charging_amp_set
            and self.ampereCharging != charging_amp_set -1
        ):
            self.ADapi.call_service('easee/set_charger_dynamic_limit',
                namespace = self.namespace,
                current = charging_amp_set,
                charger_id = self.charger_id
            )


    def startCharging(self) -> None:
        """ Starts charger.
        """
        if super().startCharging():
            try:
                self.ADapi.call_service('easee/action_command',
                    namespace = self.namespace,
                    action_command = 'resume',
                    charger_id = self.charger_id
                ) # start
            except Exception as e:
                self.ADapi.log(f"{self.charger} Could not Start Charging. Exception {e}", level = 'WARNING')


    def stopCharging(self) -> None:
        """ Stops charger.
        """
        if super().stopCharging():
            try:
                self.ADapi.call_service('easee/action_command',
                    namespace = self.namespace,
                    action_command = 'pause',
                    charger_id = self.charger_id
                ) # stop
            except Exception as e:
                self.ADapi.log(f"{self.charger} Could not Stop Charging. Exception: {e}", level = 'WARNING')


    def checkIfChargingStarted(self, kwargs) -> None:
        """ Check if charger was able to start.
        """
        if not super().checkIfChargingStarted(0):
            try:
                self.ADapi.call_service('easee/action_command',
                    namespace = self.namespace,
                    action_command = 'resume',
                    charger_id = self.charger_id
                    ) # start
            except Exception as e:
                self.ADapi.log(
                    f"Could not Start Charging in checkIfChargingStarted for {self.charger}. Exception: {e}",
                    level = 'WARNING'
                )


    def checkIfChargingStopped(self, kwargs) -> None:
        """ Check if charger was able to stop.
        """
        if not super().checkIfChargingStopped(0):
            try:
                self.ADapi.call_service('easee/action_command',
                    namespace = self.namespace,
                    action_command = 'pause',
                    charger_id = self.charger_id
                    ) # stop
            except Exception as e:
                self.ADapi.log(
                    f"Could not Stop Charging in checkIfChargingStopped for {self.charger}. Exception: {e}",
                    level = 'WARNING'
                )


class Heater:
    """ Heater
        Parent class for on_off_switch and electrical heaters
        Sets up times to save/spend based on electricity price
    """

    def __init__(self,
        api,
        heater,
        consumptionSensor,
        validConsumptionSensor:bool,
        kWhconsumptionSensor,
        max_continuous_hours:int,
        on_for_minimum:int,
        pricedrop:float,
        pricedifference_increase:float,
        namespace,
        away,
        automate,
        recipient
    ):

        self.ADapi = api

        self.heater = heater # on_off_switch boiler or heater switch

        self.namespace = namespace

        if not self.ADapi.entity_exists(away, namespace = namespace):
            self.ADapi.call_service("state/set",
                entity_id = away,
                attributes = {'friendly_name' : 'Vacation'},
                state = 'off',
                namespace = namespace
            )

        if not self.ADapi.entity_exists(automate, namespace = namespace):
            self.ADapi.call_service("state/set",
                entity_id = automate,
                attributes = {'friendly_name' : 'Automate Heater'},
                state = 'on',
                namespace = namespace
            )

            # Vacation setup
        self.away_state = self.ADapi.get_state(away, namespace = self.namespace)  == 'on'
        self.ADapi.listen_state(self.awayStateListen, away,
            namespace = self.namespace
        )

            # Automate setup
        self.automate = self.ADapi.get_state(automate, namespace = self.namespace)  == 'on'
        self.ADapi.listen_state(self.automateStateListen, automate,
            namespace = self.namespace
        )

            # Notification setup
        if recipient:
            self.recipients = recipient
        else:
            self.recipients = RECIPIENTS

            # Consumption sensors and setups
        self.consumptionSensor = consumptionSensor
        self.validConsumptionSensor:bool = validConsumptionSensor
        self.kWhconsumptionSensor = kWhconsumptionSensor
        self.prev_consumption:int = 0
        self.max_continuous_hours:int = max_continuous_hours
        self.reset_continuous_hours:bool = False
        self.on_for_minimum:int = on_for_minimum
        self.pricedrop:float = pricedrop
        self.pricedifference_increase:float = pricedifference_increase

            # Consumption data
        self.time_to_save:list = []
        self.turn_back_on:int = 0
        self.time_to_spend:list = []
        self.off_for_hours:int = 0
        self.kWh_consumption_when_turned_on:float = 0.0
        self.isOverconsumption:bool = False
        self.increase_now:bool = False
        self.normal_power:int = 0
        self.findConsumptionAfterTurnedOn_Handler = None
        self.registerConsumption_handler = None
        self.checkConsumption_handler = None

        self.HeatAt = None
        self.EndAt = None
        self.price:float = 0
        self.peak_hours:list = []

            # Persistent storage for consumption logging
        with open(JSON_PATH, 'r') as json_read:
            ElectricityData = json.load(json_read)
        if not self.heater in ElectricityData['consumption']:
            ElectricityData['consumption'].update(
                {self.heater : {"ConsumptionData" : {}}}
            )
            with open(JSON_PATH, 'w') as json_write:
                json.dump(ElectricityData, json_write, indent = 4)
        else:
            consumptionData = ElectricityData['consumption'][self.heater]['ConsumptionData']
            try:
                self.normal_power = float(self.ADapi.get_state(self.consumptionSensor, namespace = self.namespace))
            except Exception:
                self.normal_power = 0

            if self.normal_power > 100:
                if not "power" in ElectricityData['consumption'][self.heater]:
                    ElectricityData['consumption'][self.heater].update(
                        {"power" : self.normal_power}
                    )
                    with open(JSON_PATH, 'w') as json_write:
                        json.dump(ElectricityData, json_write, indent = 4)
            elif "power" in ElectricityData['consumption'][self.heater]:
                self.normal_power = ElectricityData['consumption'][self.heater]['power']
            if 'peak_hours' in ElectricityData['consumption'][self.heater]:
                self.peak_hours = ElectricityData['consumption'][self.heater]['peak_hours']

            # Get prices to set up automation times
        self.ADapi.run_in(self.heater_getNewPrices, 60)


    def awayStateListen(self, entity, attribute, old, new, kwargs) -> None:
        """ Listen for changes in vacation switch and requests heater to set new state
        """
        self.away_state = new == 'on'
        self.ADapi.run_in(self.heater_setNewValues, 5)


    def automateStateListen(self, entity, attribute, old, new, kwargs) -> None:
        """ Listen for changes to automate switch and requests heater to set new state if automation is turned back on
        """
        self.automate = new == 'on'
        self.ADapi.run_in(self.heater_setNewValues, 5)


    def heater_getNewPrices(self, kwargs) -> None:
        """ Updates time to save and spend based on ELECTRICITYPRICE.findpeakhours()
            Will also find cheapest times to heat hotwater boilers and other on/off switches when on vacation.
        """
        self.time_to_save, self.off_for_hours, self.turn_back_on, self.peak_hours = ELECTRICITYPRICE.findpeakhours(
            pricedrop = self.pricedrop,
            max_continuous_hours = self.max_continuous_hours,
            on_for_minimum = self.on_for_minimum,
            pricedifference_increase = self.pricedifference_increase,
            reset_continuous_hours = self.reset_continuous_hours,
            prev_peak_hours = self.peak_hours
        )

        if (
            self.away_state
            and len(ELECTRICITYPRICE.elpricestoday) > 25
        ):
            self.HeatAt, self.EndAt, self.price = ELECTRICITYPRICE.getContinuousCheapestTime(
                hoursTotal = 3,
                calculateBeforeNextDayPrices = False,
		        finishByHour = 14
            )

        elif (
            self.away_state
            and self.HeatAt == None
        ):
            self.HeatAt, self.EndAt, self.price = ELECTRICITYPRICE.getContinuousCheapestTime(
                hoursTotal = 3,
                calculateBeforeNextDayPrices = True,
		        finishByHour = 24
            )

        elif not self.away_state:
            self.HeatAt = None
            self.EndAt = None
        self.ADapi.run_in(self.heater_setNewValues, 5)


        """Logging purposes to check what hours heater turns off/down to check if behaving as expected"""
        #if len(self.time_to_save) > 0:
        #    self.ADapi.log(
        #        f"{self.heater} is off for {self.off_for_hours} and turns back on at {self.turn_back_on}: "
        #        f"{ELECTRICITYPRICE.print_peaks(self.time_to_save)}",
        #        level = 'INFO'
        #    )


    def heater_setNewValues(self, kwargs) -> None:
        """ Turns heater on or off based on this hours electricity price.
        """
        isOn:bool = self.ADapi.get_state(self.heater, namespace = self.namespace) == 'on'

        if (
            self.isOverconsumption
            and isOn
        ):
            self.ADapi.call_service('switch/turn_off',
                entity_id = self.heater,
                namespace = self.namespace
            )
            return

        if self.increase_now:
            if not isOn:
                self.ADapi.call_service('switch/turn_on',
                    entity_id = self.heater,
                    namespace = self.namespace
                )
            return

        if (
            datetime.datetime.today().replace(minute=0, second=0, microsecond=0) in self.time_to_save
            and self.automate
            and not CHARGE_SCHEDULER.isChargingTime()
        ):
            if isOn:
                self.ADapi.call_service('switch/turn_off',
                    entity_id = self.heater,
                    namespace = self.namespace
                )
            return

        elif not isOn:
            if (
                self.HeatAt != None
                and self.away_state
            ):
                if (
                    datetime.datetime.today() > self.HeatAt
                    and datetime.datetime.today() < self.EndAt
                    or ELECTRICITYPRICE.elpricestoday[datetime.datetime.today().hour] <= self.price + (self.pricedrop/2)
                ):
                    self.ADapi.call_service('switch/turn_on',
                        entity_id = self.heater,
                        namespace = self.namespace
                    )
                return
            else:
                self.ADapi.call_service('switch/turn_on',
                    entity_id = self.heater,
                    namespace = self.namespace
                )
                return

        elif(
            isOn
            and self.HeatAt != None
            and self.away_state
        ):
            if (
                datetime.datetime.today() > self.HeatAt
                and datetime.datetime.today() < self.EndAt
                or ELECTRICITYPRICE.elpricestoday[datetime.datetime.today().hour] <= self.price + (self.pricedrop/2)
            ):
                return
            if float(self.ADapi.get_state(self.consumptionSensor, namespace = self.namespace)) > 20:
                self.ADapi.listen_state(self.turnOffHeaterAfterConsumption, self.consumptionSensor,
                    namespace = self.namespace,
                    constrain_state=lambda x: float(x) < 20
                )
                return

            self.ADapi.call_service('switch/turn_off',
                entity_id = self.heater,
                namespace = self.namespace
            )


    def turnOffHeaterAfterConsumption(self, entity, attribute, old, new, kwargs) -> None:
        """ Turns off heater after consumption is below 20W
        """
        self.ADapi.call_service('switch/turn_off',
            entity_id = self.heater,
            namespace = self.namespace
        )


    def turn_on_heater(self) -> None:
        """ Turns heater back to normal operation after fire.
        """
        self.ADapi.run_in(self.heater_setNewValues, 5)


    def turn_off_heater(self) -> None:
        """ Turns heater off.
        """
        self.ADapi.call_service('switch/turn_off',
            entity_id = self.heater,
            namespace = self.namespace
        )


        # Functions called from electrical
    def setPreviousState(self) -> None:
        """ Set heater to previous state after overconsumption.
        """
        self.isOverconsumption = False
        self.ADapi.run_in(self.heater_setNewValues, 5)


    def setSaveState(self) -> None:
        """ Set heater to save state when overconsumption.
        """
        self.isOverconsumption = True
        self.ADapi.run_in(self.heater_setNewValues, 1)


    def setIncreaseState(self) -> None:
        """ Set heater to increase temperature when electricity production is higher that consumption.
        """
        self.increase_now = True
        self.ADapi.run_in(self.heater_setNewValues, 1)


        # Functions to calculate and log consumption to persistent storage
    def findConsumptionAfterTurnedOn(self, kwargs) -> None:
        """ Starts to listen for how much heater consumes after it has been in save mode.
        """
        try:
            self.kWh_consumption_when_turned_on = float(self.ADapi.get_state(self.kWhconsumptionSensor, namespace = self.namespace))
        except ValueError:
            self.ADapi.log(
                f"{self.kWhconsumptionSensor} unavailable in finding consumption to register after heater is turned back on",
                level = 'DEBUG'
            )
        else:
            if self.findConsumptionAfterTurnedOn_Handler != None:
                if self.ADapi.timer_running(self.findConsumptionAfterTurnedOn_Handler):
                    try:
                        self.ADapi.cancel_timer(self.findConsumptionAfterTurnedOn_Handler)
                    except Exception as e:
                        self.ADapi.log(
                            f"Not able to stop findConsumptionAfterTurnedOn_Handler for {self.heater}. Exception: {e}",
                            level = 'DEBUG'
                        )

            self.findConsumptionAfterTurnedOn_Handler = None
            if (
                self.ADapi.get_state(self.heater, namespace = self.namespace) != 'off'
                and not self.away_state
                and self.automate
            ):
                self.registerConsumption_handler = self.ADapi.listen_state(self.registerConsumption, self.consumptionSensor,
                    namespace = self.namespace,
                    constrain_state=lambda x: float(x) < 20
                )
                if self.checkConsumption_handler != None:
                    if self.ADapi.timer_running(self.checkConsumption_handler):
                        try:
                            self.ADapi.cancel_timer(self.checkConsumption_handler)
                        except Exception as e:
                            self.ADapi.log(
                                f"Not able to stop checkConsumption_handler for {self.heater}. Exception: {e}",
                                level = 'DEBUG'
                            )
                self.checkConsumption_handler = self.ADapi.run_in(self.checkIfConsumption, 1200)


    def checkIfConsumption(self, kwargs) -> None:
        """ Checks if there is consumption after 'findConsumptionAfterTurnedOn' starts listening.
            If there is no consumption it will cancel the timer.
        """
        if self.isOverconsumption:
            if self.checkConsumption_handler != None:
                if self.ADapi.timer_running(self.checkConsumption_handler):
                    try:
                        self.ADapi.cancel_timer(self.checkConsumption_handler)
                    except Exception as e:
                        self.ADapi.log(
                            f"Not able to stop checkConsumption_handler for {self.heater}. Exception: {e}",
                            level = 'DEBUG'
                        )
            self.checkConsumption_handler = self.ADapi.run_in(self.checkIfConsumption, 600)
            return
        wattconsumption:float = 0
        try:
            wattconsumption = float(self.ADapi.get_state(self.consumptionSensor, namespace = self.namespace))
        except ValueError:
            self.ADapi.log(
                f"{self.consumptionSensor} unavailable in finding consumption.",
                level = 'DEBUG'
            )
        if (
            wattconsumption < 20
            and self.registerConsumption_handler != None
        ):
            try:
                self.ADapi.cancel_listen_state(self.registerConsumption_handler)
            except Exception as e:
                self.ADapi.log(
                    f"Not able to stop registerConsumption_handler for {self.heater}. Exception: {e}",
                    level = 'DEBUG'
                )
            self.registerConsumption_handler = None


    def registerConsumption(self, entity, attribute, old, new, kwargs) -> None:
        """ Registers consumption to persistent storage after heater has been off.
        """
        if self.isOverconsumption:
            if self.checkConsumption_handler != None:
                if self.ADapi.timer_running(self.checkConsumption_handler):
                    try:
                        self.ADapi.cancel_timer(self.checkConsumption_handler)
                    except Exception as e:
                        self.ADapi.log(
                            f"Not able to stop checkConsumption_handler for {self.heater}. Exception: {e}",
                            level = 'DEBUG'
                        )
            self.checkConsumption_handler = self.ADapi.run_in(self.checkIfConsumption, 600)
            return

        consumption:float = 0
        try:
            consumption = float(self.ADapi.get_state(self.kWhconsumptionSensor, namespace = self.namespace)) - self.kWh_consumption_when_turned_on
        except (TypeError, AttributeError) as ve:
            self.ADapi.log(
                f"Could not get consumption for {self.heater} to register data. {consumption} Error: {ve}",
                level = 'DEBUG'
            )
            return

        if consumption > 0:
            if self.registerConsumption_handler != None:
                try:
                    self.ADapi.cancel_listen_state(self.registerConsumption_handler)
                except Exception as e:
                    self.ADapi.log(
                        f"Not able to stop registerConsumption_handler for {self.heater}. Exception: {e}",
                        level = 'DEBUG'
                    )
                self.registerConsumption_handler = None

            try:
                with open(JSON_PATH, 'r') as json_read:
                    ElectricityData = json.load(json_read)

                consumptionData = ElectricityData['consumption'][self.heater]['ConsumptionData']
                out_temp_str = str(math.floor(OUT_TEMP / 2.) * 2)
                
                offForHours = str(self.off_for_hours)

                if not "power" in ElectricityData['consumption'][self.heater]:
                    ElectricityData['consumption'][self.heater].update(
                        {"power" : float(old)}
                    )

                if not offForHours in ElectricityData['consumption'][self.heater]['ConsumptionData']:
                    newData = {"Consumption" : consumption, "Counter" : 1}
                    ElectricityData['consumption'][self.heater]['ConsumptionData'].update(
                        {offForHours : {out_temp_str : newData}}
                    )

                elif not out_temp_str in ElectricityData['consumption'][self.heater]['ConsumptionData'][offForHours]:
                    newData = {"Consumption" : round(consumption,2), "Counter" : 1}
                    ElectricityData['consumption'][self.heater]['ConsumptionData'][offForHours].update(
                        {out_temp_str : newData}
                    )

                else:
                    consumptionData = ElectricityData['consumption'][self.heater]['ConsumptionData'][offForHours][out_temp_str]
                    counter = consumptionData['Counter'] + 1

                    avgConsumption = round(((consumptionData['Consumption'] * consumptionData['Counter']) + consumption) / counter,2)
                    if counter > 100:
                        counter = 10
                    newData = {"Consumption" : avgConsumption, "Counter" : counter}
                    ElectricityData['consumption'][self.heater]['ConsumptionData'][offForHours].update(
                        {out_temp_str : newData}
                    )

                with open(JSON_PATH, 'w') as json_write:
                    json.dump(ElectricityData, json_write, indent = 4)

            except Exception as e:
                self.ADapi.log(
                    f"Not able to register consumption for {self.heater}. Exception: {e}",
                    level = 'DEBUG'
                )


        # Helper functions for windows
    def windowOpened(self, entity, attribute, old, new, kwargs) -> None:
        """ Reacts to windows opened.
        """
        if self.numWindowsOpened() != 0:
            self.windows_is_open = True
            self.notify_on_window_closed = True
            self.ADapi.run_in(self.heater_setNewValues, 0)


    def windowClosed(self, entity, attribute, old, new, kwargs) -> None:
        """ Reacts to windows closed and checks if other windows are opened.
        """
        if self.numWindowsOpened() == 0:
            self.windows_is_open = False
            self.notify_on_window_open = True
            self.ADapi.run_in(self.heater_setNewValues, 0)


    def numWindowsOpened(self) -> int:
        """ Returns number of windows opened.
        """
        opened = 0
        for window in self.windowsensors:
            if self.ADapi.get_state(window, namespace = self.namespace) == 'on':
                opened += 1
        return opened


class Climate(Heater):
    """ Child class of Heater
        For controlling electrical heaters to heat off peak hours.
    """

    def __init__(self,
        api,
        heater,
        consumptionSensor,
        validConsumptionSensor:bool,
        kWhconsumptionSensor,
        max_continuous_hours:int,
        on_for_minimum:int,
        pricedrop:float,
        pricedifference_increase:float,
        namespace:str,
        away,
        automate,
        recipient,
        indoor_sensor_temp,
        window_temp,
        window_offset:float,
        target_indoor_input,
        target_indoor_temp:float,
        save_temp_offset:float,
        save_temp:float,
        away_temp:float,
        rain_level:float,
        anemometer_speed:int,
        low_price_max_continuous_hours:int,
        priceincrease:float,
        windowsensors:list,
        getting_cold:int,
        daytime_savings:list,
        temperatures:list
    ):

            # Sensors
        self.indoor_sensor_temp = indoor_sensor_temp
        if target_indoor_input != None:
            api.listen_state(self.updateTarget, target_indoor_input,
                namespace = namespace
            )
            self.target_indoor_temp = float(api.get_state(target_indoor_input, namespace = namespace))
        else:
            self.target_indoor_temp:float = target_indoor_temp
        self.save_temp_offset = save_temp_offset
        self.save_temp = save_temp
        self.away_temp = away_temp
        self.window_temp = window_temp
        self.window_offset:float = window_offset
        self.rain_level:float = rain_level
        self.anemometer_speed:int = anemometer_speed
        self.low_price_max_continuous_hours:int = low_price_max_continuous_hours
        self.priceincrease:float = priceincrease
        self.windowsensors:list = windowsensors
        self.daytime_savings:list = daytime_savings
        self.temperatures:list = temperatures

        super().__init__(
            api = api,
            heater = heater,
            consumptionSensor = consumptionSensor,
            validConsumptionSensor = validConsumptionSensor,
            kWhconsumptionSensor = kWhconsumptionSensor,
            max_continuous_hours = max_continuous_hours,
            on_for_minimum = on_for_minimum,
            pricedrop = pricedrop,
            pricedifference_increase = pricedifference_increase,
            namespace = namespace,
            away = away,
            automate = automate,
            recipient = recipient
        )

        self.reset_continuous_hours = True
        self.getting_cold:float = getting_cold

        self.windows_is_open:bool = False
        for window in self.windowsensors:
            if self.ADapi.get_state(window, namespace = self.namespace) == 'on':
                self.windows_is_open = True

        for windows in self.windowsensors:
            self.ADapi.listen_state(self.windowOpened, windows,
                new = 'on',
                duration = 120,
                namespace = self.namespace
            )
            self.ADapi.listen_state(self.windowClosed, windows,
                new = 'off',
                namespace = self.namespace
            )


        self.notify_on_window_open:bool = True
        self.notify_on_window_closed:bool = False

        try:
            self.min_temp = self.ADapi.get_state(self.heater,
                namespace = self.namespace,
                attribute = 'min_temp'
            )
        except (ValueError, TypeError) as ve:
            self.ADapi.log(
                f"{self.heater} Attribute = 'min_temp' is not found in: "
                f"{self.ADapi.get_state(self.heater, namespace = self.namespace, attribute = 'all')} "
                f"ValueError: {ve}",
                level = 'DEBUG'
            )
            self.min_temp = 5

        # Set up runs
        runtime = datetime.datetime.now()
        addseconds = (round((runtime.minute*60 + runtime.second)/1200)+1)*1200
        runtime = runtime.replace(minute=0, second=10, microsecond=0) + datetime.timedelta(seconds=addseconds)
        self.ADapi.run_every(self.heater_setNewValues, runtime, 1200)


        # Get new prices to save and in addition to turn up heat for heaters before expensive hours
    def heater_getNewPrices(self, kwargs) -> None:
        """ Updates time to save and spend based on ELECTRICITYPRICE.findpeakhours() and findLowPriceHours()
        """
        super().heater_getNewPrices(0)
        self.time_to_spend = ELECTRICITYPRICE.findLowPriceHours(
            priceincrease = self.priceincrease,
            max_continuous_hours = self.low_price_max_continuous_hours
        )

        """Logging purposes to check what hours heating will be turned up"""
        #if self.time_to_spend:
        #    self.ADapi.log(f"{self.heater} Extra heating at: {ELECTRICITYPRICE.print_peaks(self.time_to_spend)}", level = 'INFO')


    def awayStateListen(self, entity, attribute, old, new, kwargs) -> None:
        """ Listen for changes in vacation switch and requests heater to set new state
        """
        self.away_state = new == 'on'
        if (
            self.ADapi.get_state(self.heater, namespace = self.namespace) == 'off'
            and new == 'off'
        ):
            try:
                self.ADapi.call_service('climate/set_hvac_mode',
                    namespace = self.namespace,
                    entity_id = self.heater,
                    hvac_mode = 'heat'
                )
            except Exception as e:
                self.ADapi.log(f"Not able to set hvac_mode to heat for {self.heater}. Exception: {e}", level = 'INFO')
        self.ADapi.run_in(self.heater_setNewValues, 5)


    def find_target_temperatures(self) -> int:
        """ Helper function to find correct dictionary element in temperatures
        """
        target_num = 0
        for target_num, target_temp in enumerate(self.temperatures):
            if target_temp['out'] >= OUT_TEMP:
                if target_num != 0:
                    target_num -= 1
                return target_num

        return target_num


    def turn_on_heater(self) -> None:
        """ Turns climate on after fire alarm.
        """
        self.ADapi.call_service('climate/turn_on',
            entity_id = self.heater,
            namespace = self.namespace
        )
        self.ADapi.run_in(self.heater_setNewValues, 5)


    def turn_off_heater(self) -> None:
        """ Turns climate off.
        """
        self.ADapi.call_service('climate/turn_off',
            entity_id = self.heater,
            namespace = self.namespace
        )


        # Functions to set temperature
    def setSaveState(self) -> None:
        """ Set heater to save state when overconsumption.
        """
        self.isOverconsumption = True
        if self.ADapi.get_state(self.heater, namespace = self.namespace) == 'heat':
            target_num = self.find_target_temperatures()
            target_temp = self.temperatures[target_num]
            if self.save_temp != None:
                save_temp = self.save_temp + target_temp['offset']
            elif 'save' in target_temp:
                save_temp = target_temp['save']
            else:
                save_temp = 10
            try:
                if float(self.ADapi.get_state(self.heater, namespace = self.namespace, attribute='temperature')) > save_temp:
                    self.ADapi.call_service('climate/set_temperature',
                        namespace = self.namespace,
                        entity_id = self.heater,
                        temperature = save_temp
                    )
            except (TypeError, AttributeError) as ve:
                self.ADapi.call_service('climate/set_temperature',
                    namespace = self.namespace,
                    entity_id = self.heater,
                    temperature = 10
                )
                self.ADapi.log(f"Error when trying to set temperature to {self.heater}: {ve}", level = 'DEBUG')


    def heater_setNewValues(self, kwargs) -> None:
        """ Adjusts temperature based on weather and time to save/spend
        """
        if (
            self.ADapi.get_state(self.heater, namespace = self.namespace) == 'off'
            or self.isOverconsumption
        ):
            return

        target_num = self.find_target_temperatures()
        target_temp = self.temperatures[target_num]

        try:
            heater_temp = float(self.ADapi.get_state(self.heater, namespace = self.namespace, attribute='temperature'))
        except (ValueError, TypeError) as ve:
            self.ADapi.log(
                f"Error when trying to get currently set temperature to {self.heater}: {ve}",
                level = 'DEBUG'
            )
            heater_temp = self.target_indoor_temp
        except Exception as e:
            self.ADapi.log(
                f"Error when trying to get currently set temperature to {self.heater}. Exception: {e}",
                level = 'INFO'
            )
            heater_temp = self.target_indoor_temp

        in_temp:float = -50
        if self.indoor_sensor_temp != None:
            try:
                in_temp = float(self.ADapi.get_state(self.indoor_sensor_temp, namespace = self.namespace))
            except (TypeError, AttributeError) as te:
                self.ADapi.log(f"{self.heater} has no temperature. Probably offline", level = 'DEBUG')
            except Exception as e:
                self.ADapi.log(
                    f"Not able to get new inside temperature from {self.indoor_sensor_temp}. Error: {e}",
                    level = 'DEBUG'
                )
        if in_temp == -50:
            try:
                in_temp = float(self.ADapi.get_state(self.heater, namespace = self.namespace, attribute='current_temperature'))
                self.ADapi.log(
                    f"{self.heater} Not able to get new inside temperature from {self.indoor_sensor_temp}. "
                    f"Getting in temp from heater. It is: {in_temp}",
                    level = 'DEBUG'
                )
            except (TypeError, AttributeError) as te:
                self.ADapi.log(f"{self.heater} has no temperature. Probably offline. Error: {te}", level = 'DEBUG')
            except Exception as e:
                self.ADapi.log(f"Not able to get new inside temperature from {self.heater}. {e}", level = 'WARNING')


        # Set Target temperatures
        if 'offset' in target_temp:
            new_temperature = self.target_indoor_temp + target_temp['offset']
        elif 'normal' in target_temp:
            new_temperature = target_temp['normal']
        else:
            new_temperature = self.target_indoor_temp

        if self.away_temp != None:
            away_temp = self.away_temp + target_temp['offset']
        elif 'away' in target_temp:
            away_temp = target_temp['away']
        else:
            away_temp = 5

        # Adjust temperature based on weather
        if RAIN_AMOUNT >= self.rain_level:
            new_temperature += 1
        elif WIND_AMOUNT >= self.anemometer_speed:
            new_temperature += 1
        
        adjust = 0
        if self.window_temp != None:
            try:
                window_temp = float(self.ADapi.get_state(self.window_temp, namespace = self.namespace))
            except (TypeError, AttributeError):
                window_temp = self.target_indoor_temp + self.window_offset
                self.ADapi.log(f"{self.window_temp} has no temperature. Probably offline", level = 'DEBUG')
            except Exception as e:
                window_temp = self.target_indoor_temp + self.window_offset
                self.ADapi.log(f"Not able to get temperature from {self.window_temp}. {e}", level = 'DEBUG')
            if window_temp > self.target_indoor_temp + self.window_offset:
                adjust = math.floor(float(window_temp - (self.target_indoor_temp + self.window_offset)))

        if in_temp > self.target_indoor_temp:
            adjust += math.floor(float(in_temp - self.target_indoor_temp))
        
        new_temperature -= adjust

        if new_temperature < away_temp:
            new_temperature = away_temp


        # Windows
        if (
            not self.windows_is_open
            and self.notify_on_window_closed
            and in_temp >= self.target_indoor_temp + 10
            and OUT_TEMP > self.getting_cold
        ):
            NOTIFY_APP.send_notification(
                message = f"No Window near {self.heater} is open and it is getting hot inside! {in_temp}°",
                message_title = f"Window closed",
                message_recipient = RECIPIENTS,
                also_if_not_home = False
            )
            self.notify_on_window_closed = False
        
        if self.windows_is_open:
            new_temperature = away_temp
            if (
                self.notify_on_window_open
                and OUT_TEMP < self.getting_cold
                and in_temp < self.getting_cold
            ):
                NOTIFY_APP.send_notification(
                    message = f"Window near {self.heater} is open and inside temperature is {in_temp}°",
                    message_title = "Window open",
                    message_recipient = RECIPIENTS,
                    also_if_not_home = False
                )
                self.notify_on_window_open = False


        # Holliday temperature
        elif self.away_state:
            new_temperature = away_temp

        # Peak and savings temperature
        if (
            datetime.datetime.today().replace(minute=0, second=0, microsecond=0) in self.time_to_save
            and self.automate
        ):
            new_temperature = self.getSaveTemp(new_temperature, target_temp)
        
        # Daytime Savings
        else:
            doDaytimeSaving = False
            for daytime in self.daytime_savings:
                if (
                    'start' in daytime
                    and 'stop' in daytime
                ):
                    if self.ADapi.now_is_between(daytime['start'], daytime['stop']):
                        doDaytimeSaving = True
                        if 'presence' in daytime:
                            for presence in daytime['presence']:
                                if self.ADapi.get_state(presence, namespace = self.namespace) == 'home':
                                    doDaytimeSaving = False

                elif 'presence' in daytime:
                    doDaytimeSaving = True
                    for presence in daytime['presence']:
                        if self.ADapi.get_state(presence, namespace = self.namespace) == 'home':
                            doDaytimeSaving = False

            if doDaytimeSaving:
                new_temperature = self.getSaveTemp(new_temperature, target_temp)


        # Low price for electricity or solar power
        if (
            self.increase_now
            or datetime.datetime.today().replace(minute=0, second=0, microsecond=0) in self.time_to_spend
        ):
            new_temperature += 1

        # Avoid setting lower temp than climate minimum
        if new_temperature < self.min_temp:
            new_temperature = self.min_temp

        # Setting new temperature
        try:
            if heater_temp != new_temperature:
                self.ADapi.call_service('climate/set_temperature',
                    namespace = self.namespace,
                    entity_id = self.heater,
                    temperature = new_temperature
                )
        except (TypeError, AttributeError):
            self.ADapi.log(f"{self.heater} has no temperature. Probably offline", level = 'DEBUG')


    def getSaveTemp(self, new_temperature:float, target_temp:dict) -> float:
        """ Returns save temperature
        """
        if self.save_temp_offset != None:
            new_temperature += self.save_temp_offset
        elif self.save_temp != None:
            if new_temperature > self.save_temp + target_temp['offset']:
                new_temperature = self.save_temp + target_temp['offset']
        elif 'save' in target_temp:
            if new_temperature > target_temp['save']:
                new_temperature = target_temp['save']
        else:
            new_temperature = 10

        return new_temperature


    def updateTarget(self, entity, attribute, old, new, kwargs):
        """ Reacts to target temperature for room beening updated.
        """
        self.target_indoor_temp = float(new)
        self.ADapi.run_in(self.heater_setNewValues, 5)


class On_off_switch(Heater):
    """ Child class of Heater
        Heating of on_off_switch off peak hours
        Turns on/off a switch depending og given input and electricity price
    """
    def __init__(self,
        api,
        heater,
        consumptionSensor,
        validConsumptionSensor:bool,
        kWhconsumptionSensor,
        max_continuous_hours:int,
        on_for_minimum:int,
        pricedrop:float,
        pricedifference_increase:float,
        namespace,
        away,
        automate,
        recipient
    ):

        self.daytime_savings:list = []

        super().__init__(
            api = api,
            heater = heater,
            consumptionSensor = consumptionSensor,
            validConsumptionSensor = validConsumptionSensor,
            kWhconsumptionSensor = kWhconsumptionSensor,
            max_continuous_hours = max_continuous_hours,
            on_for_minimum = on_for_minimum,
            pricedrop = pricedrop,
            pricedifference_increase = pricedifference_increase,
            namespace = namespace,
            away = away,
            automate = automate,
            recipient = recipient
        )

        self.ADapi.run_hourly(self.heater_setNewValues, datetime.time(0, 0, 2))


class Appliances:
    """ Appliances
        Starting of appliances when electricity price is lowest before next time defined by 'finishByHour'.
        If requested between '06:00:00', '15:00:00' it will try to finish before 15:00.
    """

    def __init__(self,
        api,
        remote_start,
        nightprogram,
        dayprogram,
        namespace:str,
        away
    ):

        self.ADapi = api
        self.handler = None
        self.namespace = namespace

            # Vacation setup
        self.away_state = self.ADapi.get_state(away, namespace = self.namespace)  == 'on'
        self.ADapi.listen_state(self.awayStateListen, away,
            namespace = self.namespace
        )
            # Program setup
        self.remote_start = remote_start
        self.nightprogram = nightprogram
        if not 'running_time' in self.nightprogram:
            self.nightprogram.update(
                {'running_time': 4}
            )
        self.dayprogram = dayprogram
        if not 'running_time' in self.dayprogram:
            self.dayprogram.update(
                {'running_time': 4}
            )

        self.ADapi.listen_state(self.remoteStartRequested, remote_start,
            new = 'on',
            namespace = self.namespace
        )

        if (
            self.ADapi.get_state(remote_start, namespace = self.namespace) == 'on'
            and not self.away_state
        ):
            self.ADapi.run_in(self.findTimeForWashing,70)


        self.dayruntime = self.ADapi.parse_datetime('07:00:00') - datetime.timedelta(hours=int(self.nightprogram['running_time']))
        self.dayruntime_stop = self.ADapi.parse_datetime('16:00:00') - datetime.timedelta(hours=int(self.dayprogram['running_time']))

    def remoteStartRequested(self, entity, attribute, old, new, kwargs) -> None:
        """ Remote start signal received.
        """
        self.ADapi.run_in(self.findTimeForWashing,5)


    def findTimeForWashing(self, kwargs) -> None:
        """ Finds cheapest time to run appliance.
        """
        if self.ADapi.now_is_between(str(self.dayruntime.time()), str(self.dayruntime_stop.time())):
            # Run during daytime
            startWashingAt, EndAt, price = ELECTRICITYPRICE.getContinuousCheapestTime(
                hoursTotal = self.dayprogram['running_time'],
                calculateBeforeNextDayPrices = True,
                finishByHour = 16
            )

            if startWashingAt is not None:
                if startWashingAt > datetime.datetime.today():
                    self.resetHandler()
                    self.handler = self.ADapi.run_at(self.startWashing, startWashingAt,
                        program = self.dayprogram['program']
                    )
                    NOTIFY_APP.send_notification(
                        message = f"Starting {self.ADapi.get_state(self.dayprogram['program'], attribute='friendly_name', namespace = self.namespace)} "
                        f"at {startWashingAt}",
                        message_title = "Appliances",
                        message_recipient = RECIPIENTS,
                        also_if_not_home = False
                    )
                    return


        else:
            # Run during nighttime
            startWashingAt, EndAt, price = ELECTRICITYPRICE.getContinuousCheapestTime(
                hoursTotal = self.nightprogram['running_time'],
                calculateBeforeNextDayPrices = False,
                finishByHour = 8
            )

            if startWashingAt is not None:
                if startWashingAt > datetime.datetime.today():
                    self.resetHandler()
                    self.handler = self.ADapi.run_at(self.startWashing, startWashingAt,
                        program = self.nightprogram['program']
                    )
                    NOTIFY_APP.send_notification(
                        message = f"Starting {self.ADapi.get_state(self.nightprogram['program'], attribute='friendly_name', namespace = self.namespace)} "
                        f"at {startWashingAt}",
                        message_title = "Appliances",
                        message_recipient = RECIPIENTS,
                        also_if_not_home = False
                    )
                    return

        self.ADapi.run_in(self.startWashing, 10,
            program = self.nightprogram['program']
        )


    def startWashing(self, **kwargs) -> None:
        """ Starts appliance
        """
        program = kwargs['program']
        if (
            self.ADapi.get_state(program, namespace = self.namespace) == 'off'
            and self.ADapi.get_state(self.remote_start, namespace = self.namespace) == 'on'
            and not self.away_state
        ):
            self.ADapi.call_service('switch/turn_on',
                entity_id = program,
                namespace = self.namespace
            )


    def resetHandler(self) -> None:
        """ Resets handler for appliance before setting up a new runtime.
        """
        if self.handler != None:
            if self.ADapi.timer_running(self.handler):
                try:
                    self.ADapi.cancel_timer(self.handler)
                except Exception as e:
                    self.ADapi.log(f"Not possible to stop timer for appliance. {e}", level = 'DEBUG')
            self.handler = None

    def awayStateListen(self, entity, attribute, old, new, kwargs) -> None:
        """ Listen for changes in vacation switch to prevent application to start when on vacation.
        """
        self.away_state = new == 'on'


class Notify_Mobiles:
    """ Class to send notification with 'notify' HA integration
    """
    def __init__(self, api,
        namespace:str
    ) -> None:
        self.ADapi = api
        self.namespace = namespace


    def send_notification(self, **kwargs) -> None:
        """ Sends notification to recipients via Home Assistant notification.
        """
        message:str = kwargs['message']
        message_title:str = kwargs.get('message_title', 'Home Assistant')
        message_recipient:str = kwargs.get('message_recipient', True)
        also_if_not_home:bool = kwargs.get('also_if_not_home', False)
        data:dict = kwargs.get('data', {'clickAction' : 'noAction'})

        for re in message_recipient:
            self.ADapi.call_service(f'notify/{re}',
                title = message_title,
                message = message,
                data = data,
                namespace = self.namespace
            )
