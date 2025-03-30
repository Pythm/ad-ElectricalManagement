# ad-Electrical Management

The purpose of this app is to help reduce your electricity bill by:
- Automating charging times for electric vehicles (EVs), so they charge during off-peak hours when electricity rates are lower.
- Turning up/down heating sources and on/off hot water boilers based on electricity prices.

# Breaking configuration
From the release 0.2.0 you now configure cars (including onboard charing) as `cars` and connected chargers as `chargers`.
[Check chapter on how to set up charger and car.](https://github.com/Pythm/ad-ElectricalManagement?tab=readme-ov-file#charging)


## What platforms does it support?
This app is designed to work with [AppDaemon](https://github.com/AppDaemon/appdaemon) and [Home Assistant](https://www.home-assistant.io/).

Home Assistant is a popular open-source home automation platform that offers a wide range of features and integrations with various smart home devices. If you're not already using Home Assistant, I recommend checking it out.

AppDaemon is a loosely coupled, multi-threaded, sandboxed Python execution environment for writing automation apps for various types of home automation software, including Home Assistant and MQTT.


## How does it work?
The app calculates the optimal charging time for your electric car based on your historical electricity consumption where it takes into account factors like weather conditions, in addition to current rates, and future prices from the [Nordpool custom components](https://github.com/custom-components/nordpool).

For heating sources and hot water boilers, the app uses similar calculations to determine when to turn them on/off/down based on the lowest possible electricity rates while still ensuring comfort needs are met. It can also turn up heating sources before a price increase. The app continuously monitors your energy consumption and adjusts settings accordingly, helping you avoid peak hours when electricity rates are higher and maximize savings during off-peak hours.

> [!TIP]
> I use sensors from Tibber Pulse connected to HAN port. Check out https://tibber.com/ If you are interested in changeing your electricity provider to Tibber, you can use my invite link to get a startup bonus: https://invite.tibber.com/fydzcu9t

> [!NOTE]
> Max usage limit `max_kwh_goal` is developed according to the calculation that Norwegian Energy providers base their grid tariffs on. We pay extra for the average of the 3 highest peak loads in steps of 2-5 kWh, 5-10 kWh, etc. This should be adaptable to other tariffs with some modifications. Please make a request with information on how to set up limitations on usage.

> [!TIP]
> If you live in a country where there is no tariff on higher usage, set `max_kwh_goal` to the same size as your main fuse in kWh. It defaults to 15.

If you have solar or other electricity production, add a production sensor and an accumulated production sensor. The app will try to charge any cars with surplus production. If all cars have reached their preferred charge limit, it will try to spend extra on heating. The calculations also support one consumption sensor with negative numbers for production. I do not have solar panels installed and this feature is only tested with manual input of test data. Please report any unexpected behavior.


## Dependencies:
To use this app, install the following integrations:
From Home Assistant:
- Workday sensor: [Home Assistant Workday integration](https://www.home-assistant.io/integrations/workday/)

The app uses the Met.no for outside temperature if you do not configure `outside_temperature`: [Met.no Home Assistant integration](https://www.home-assistant.io/integrations/met/)

Install the following components via HACS:
- Nordpool sensor: [Nordpool custom components](https://github.com/custom-components/nordpool)


You only need the following optional components if they are configured in your setup. Currently supported directly in app:
- Tesla Custom Integration: [HACS Tesla integration](https://github.com/alandtse/tesla)
- Easee EV charger component for Home Assistant: [HACS Easee EV Charger integration](https://github.com/nordicopen/easee_hass)

# Installation and configuration
1. git clone into your [AppDaemon](https://appdaemon.readthedocs.io/en/latest/) `apps` directory.
2. Add configuration to a `.yaml` or `.toml` file to enable the `ElectricalManagement` module.

Minimum configuration with suggested values:

```yaml
electricity:
  module: electricalManagement
  class: ElectricalUsage 
  json_path: /conf/apps/ElectricalManagement/ElectricityData.json
  nordpool: sensor.nordpool_kwh_bergen_nok_3_10_025
  power_consumption: sensor.power_home
  accumulated_consumption_current_hour: sensor.accumulated_consumption_current_hour_home
```

Provide a consumption sensor `power_consumption` and an accumulated consumption pr hour sensor `accumulated_consumption_current_hour` to calculate and adjust electricity usage.

> [!IMPORTANT]
>  `accumulated_consumption_current_hour` is a kWh sensor that resets to zero every hour


#### Json storage
To configure storage, input a path, inclusive of a name and a .json filename (e.g., '/myfolder/example.json') to store a JSON file using the `json_path` as persistent data.

Persistent data will be updated with:
- The maximum kWh usage for the 3 highest hours.
- The maximum amperage that the vehicle can receive and the charger can deliver.
- Maximum kWh charged during one session.
- Store charging data on termination to recall when restarted.
- Heater consumptions after saving functions with hours of savings and the heater + total power in watts after finishing charging, both with the outside temperature to better calculate how many hours cars need to finish charging.
- Idle consumption when charging has finished.


### Other configurations for main functions
Set a maximum kWh limit using `max_kwh_goal` and define a `buffer`. Buffer size depends on how much of your electricity usage is controllable, and how strict you set your max kWh usage. It defaults to 0.4 as it should be a good starting point.

> [!IMPORTANT]
> The maximum usage limit per hour, `max_kwh_goal`, is by default 15 kWh. If the average of the 3 highest consumption hours exceeds this limit, it will increase by 5 kWh. If the limit is set too low, it may reduce heating, turn off switches, and change the charge current. Please define a proper value for `max_kwh_goal` according to your normal electricity usage.

Add tax per kWh from your electricity grid provider with `daytax` and `nighttax`. Night tax applies from 22:00 to 06:00 on workdays and all day on weekends. The app will also look for 'binary_sensor.workday_sensor' and set night tax on holidays. If your [Workday Sensor](https://www.home-assistant.io/integrations/workday/) has another entity ID, you can configure it with `workday`.

In Norway, we receive 90% electricity support (Strømstøtte) on electricity prices above 0.70 kr exclusive / 0.9125 kr inclusive VAT (MVA) calculated per hour. Define `power_support_above` and `support_amount` to have calculations take the support into account. Do not define if not applicable.

The app calculates the optimal charging price and schedule based on this data, ensuring a coherent time frame from start to finish. Vehicles will charge when the price is cheaper than the calculated rate. Additionally, you can customize the charging behavior by specifying a price difference between the calculated charging period with startBeforePrice (default 0.01) to start earlier if prices are still low, ensuring enough time to charge even with limited data for maximum kWh usage per hour. You can also force stop charging with stopAtPriceIncrease (default 0.3) if the charging isn't completed within the calculated time.

```yaml
  max_kwh_goal: 15 # 15 is default.
  buffer: 0.4 # 0.4 is default.

  daytax: 0.5648 # 0 is default
  nighttax: 0.4468 # 0 is default
  workday: binary_sensor.workday_sensor

  power_support_above: 0.9125 # Inkl vat
  support_amount: 0.9 # 90%

  startBeforePrice: 0.01
  stopAtPriceIncrease: 0.3
```

#### Reducing consumption to stay below max kwh goal
The app checks power consumptions and reacts to prevent using more than defined with `max_kwh_goal`. It reduces charging speed on car(s) currently charging to a minumum, before turning down heater_switches and climate entities. If it is still going over the app can pause charging if `pause_charging` is configured under `options`.

```yaml
  options:
    - pause_charging
```


### Setting vacation mode

Set a main `vacation` switch to lower temperature when away. This can be configured/overridden individually for each climate/switch entity if you are controlling multiple apartments, etc.

```yaml
  vacation: input_boolean.vacation
```


#### Notifications and information
Receive notifications about charge time to your devices with `notify_receiver`. It will also notify if you left a window open and it is getting cold, or if it is getting quite hot and the window is closed if you configure windows with heaters.

You can also create and configure an Home Assistant input_text with `infotext` to display currently planned chargetime in Home Assistant or some external displays.

```yaml
  notify_receiver:
    - mobile_app_yourphone
    - mobile_app_yourotherphone
  infotext: input_text.information
  options:
    - notify_overconsumption
    - pause_charging
```

You can also configure electricalManagement to use your own Notification app instead with `notify_app`. You'll need to have a function in your app to receive. App sends one call with kwargs: message, message_title, message_recipient and also_if_not_home. Data might contain tag to replace old notifications. More kwargs can be added later or on request.

```python
    def send_notification(self, **kwargs) -> None:
        """ Sends notification to recipients via Home Assistant notification.
        """
        message:str = kwargs['message']
        message_title:str = kwargs.get('message_title', 'Home Assistant')
        message_recipient:str = kwargs.get('message_recipient', True)
        also_if_not_home:bool = kwargs.get('also_if_not_home', False)
        data:dict = kwargs:get('data', {})

        for re in message_recipient:
            self.ADapi.call_service(f'notify/{re}',
                title = message_title,
                message = message,
                data = data,
                namespace = self.namespace
            )
```


### Weather Sensors
I recommend setting up the [ad-Weather](https://github.com/Pythm/ad-Weather) application. This tool consolidates all your weather sensors into one application, and it publishes an event to other applications whenever there is a sensor update.

Please note that any weather sensors integrated with climateCommander will take precedence over the ad-Weather app.

The app relies on the outside temperature to log and calculate electricity usage. Climate entities set heating based on the outside temperature.

In addition, you can configure rain and anemometer sensors. These are used by climate entities where you can define a rain amount `rain_level` (Defaults to 3) and wind speed `anemometer_speed` (Defaults to 40) to increase heating.

```yaml
  outside_temperature: sensor.netatmo_out_temperature
  rain_sensor: sensor.netatmo_sensor_rain
  rain_level: 3
  anemometer: sensor.netatmo_anemometer_wind_strength
  anemometer_speed: 40
```

> [!TIP]
> `anemometer_speed` and `rain_level` target can be defined pr climate entity.


### Namespace
A key feature of Appdaemon is the ability to define custom namespaces. Visit the [Appdaemon documentation](https://appdaemon.readthedocs.io/en/latest/CONFIGURE.html#) for more information.

If you have not configured any namespace for your HASS plugin in your 'appdaemon.yaml' file, you can safely ignore namespaces.

> [!IMPORTANT]
> As of version 0.1.5 you can set a namespace for heater/climate and charging entities with `main_namespace` if you have defined a custom HASS namespace. You can then configure the `namespace` in every charger and heater/climate that belongs to Home Assistant instances with another custom namespace if you are running multiple namespaces.

> :bulb: **TIP**
> The app is designed to control electricity usage at your primary residence and will only adjust charging amps on chargers/cars that are  within your home location. If you want to manage electricity consumption in other locations, I recommend setting up a separate Home Assistant and AppDaemon instance for each location.


### Mode change events
This app listens to event "MODE_CHANGE" in Home Assistant. It reacts to mode "fire" by turning off all heaters and stopping charging, and "false_alarm" to revert back to normal operations.
> The use of events in Appdaemon and Home Assistant is well documented in [Appdaemon docs - Events](https://appdaemon.readthedocs.io/en/latest/APPGUIDE.html#events)

To set mode from another appdaemon app simply use:
```python
self.fire_event("MODE_CHANGE", mode = 'your_mode_name')
```


# Charging
The app calculates electric vehicle (EV) charging time based on the State of Charge (SOC), battery size, and outside temperature. If an SOC sensor or battery size isn't provided, it will be based on the maximum charged during one session on the charger.

> [!NOTE]
> If you have other high electricity consumption in combination with a low limit, it may reduce charging down to the minimum allowd by the charger, or even stop charging if you configure with option `pause_charging`. This could result in unfinished charging if the limit is too low or consumption is too high during the calculated charge time.

The app supports controlling Tesla vehicles directly and Easee wall chargers. Documentation on how to implement other vehicles and chargers will be published upon requests.

As of version 0.2.0 you configure chargers and cars separately. The app will connect car to charger. The calulation of chargetime is done with sensors from cars.


## Configuration for chargers

#### Home Assistant helpers

Create Home Assistant helpers to manage charging:

1. **Guest Function**: There is a `guest` function defined with an `input_boolean` on chargers. This allows bypassing smart charging and avoids registering the maximum charged during one session and maximum amperage the car can charge.


### Easee Charger

> [!IMPORTANT]
> Locking Easee charger to 3phase IT net (230v) requires a minumum of 11A to charge. The app can turn down charging as low as 6A, and if so, the charging will stop. To awoid this set Phase mode to Automatic in your Easee app.

> [!NOTE]
> Note that not all sensors are activated by default in the Easee integration. Please ensure you activate all relevant sensors to enable the full functionality of the app.


The Easee integration automatically detects wall charger information if its sensor names are in English; simply provide the name of your Easee using the `charger` option. If your sensors have names in another language, manually input the correct sensor names in the configuration. Check logs for any errors and provide missing sensors.

```yaml
  easee:
    - charger: nameOfCharger
      charger_status: sensor.nameOfCharger_status
      reason_for_no_current: sensor.nameOfCharger_arsak_til_at_det_ikke_lades
      current: sensor.nameOfCharger_strom
      charger_power: sensor.nameOfCharger_effekt
      voltage: sensor.nameOfCharger_spenning
      max_charger_limit: sensor.nameOfCharger_maks_grense_for_lader
      session_energy: sensor.nameOfCharger_energi_ladesesjon
      idle_current: switch.nameOfCharger_ventestrom
```


## Configuration for cars

#### Home Assistant helpers

Create Home Assistant helpers to manage charging:

1. **Default Finish Time**: Charging is completed by default at 7:00 AM. To set a different hour for the charging to be finished, use an `input_number` sensor configured with `finishByHour`. This can also be configured with an integer directly in configuration if you do not inted to change it.
2. **Charge Now**: To bypass smart charging and charge immediately, configure `charge_now` as an `input_boolean`. The sensor will be automatically set to false after charging is completed or when the charger is disconnected.
3. **Charging on Solar Power**: If you generate electricity, you can choose to charge only during surplus production. Define another `input_boolean` and configure it with the `charge_on_solar` option.


#### Priority settings for cars
Multiple cars with priority levels 1-5 are supported by the app. The app queues the next car based on priority when there's enough power available. If multiple vehicles are in the queue, charging vehicles must reach full capacity before the next charger checks if it has 1.6 kW of free capacity to start.

Priority settings for cars include:

- Priority 1-2: These cars will begin charging at the calculated time, even if it means reducing heating to stay below the consumption limit. They will continue charging until complete, regardless of any price increases due to adjusting the speed based on the consumption limit.

- Priority 3-5: These cars will wait to start charging until there is 1.6 kW of free capacity available. They will stop charging at the price increase that occurs after the calculated charge time ends.


### Configuring Tesla

> :warning: **WARNING**
> It is necessary to restart Home Assistant, and in some cases, reboot the vehicle to re-establish communications with the Tesla API after any service visits and also changes/reconfiguration of the integration in Home Assistant, such as if you need to update the API key.

Input the name of your Tesla using the `charger` option. Check logs for any errors and provide missing sensors.

```yaml
  tesla:
    - charger: nameOfCar
      pref_charge_limit: 90
      battery_size: 100
      finishByHour: input_number.charing_finished
      priority: 3
      charge_now: input_boolean.charge_now
```

### Defining another vehicle to charge

If you have a vehicle you can define it with the following sensors under `cars`

- `carName`: Name of car.
- `charger_sensor`: Charge cable Connected or Disconnected
- `charge_limit`: SOC limit sensor in %
- `battery_sensor`: SOC (State Of Charge) in %
- `asleep_sensor`: If car is sleeping
- `online_sensor`: If car is online
- `location_tracker`: Location of car/charger.
- `software_update`: If cars updates software it probably can`t change charge speed or stop charging
- `force_data_update`: Button to Force Home Assistant to pull new data
- `polling_switch`: Home Assistant input_boolean to disable pulling data from car
- `data_last_update_time`: Last time Home Assistant pulled data
- `battery_size`: Size of battery in kWh
- `pref_charge_limit`: Preferred chargelimit

in addition to the HA helpers.

Note that this is not tested. Please report any unvanted behaviour.

> [!WARNING]
> The default location if no sensor for location is provided is 'home'. This will stop charging if you are controlling your car and not a wall charger if it is not chargetime. Please make sure your location sensor is functioning properly


# Climate
Here you configure climate entities that you want to control based on outside temperature and electricity price.

> For HVAC and other climate entities that are not very power consuming, you should check out [Climate Commander](https://github.com/Pythm/ad-ClimateCommander). That app is written to keep a constant inside temperature.

Climate entities are defined under `climate` and you configure the temperature it should heat to, based on the outside temperature. You configure it either by `name` or with the entity ID as `heater`, and the app will attempt to find consumption sensors based on some default zwave naming, or you can define current consumption using the `consumptionSensor` for the current consumption, and `kWhconsumptionSensor` for the total kWh that the climate has used.

> [!IMPORTANT]
> If no `consumptionSensor` or `kWhconsumptionSensor` is found or configured, the app will log with a warning to your AppDaemon log.

If the heater does not have a consumption sensor, you can input its `power` in watts. The app uses this power to calculate how many heaters to turn down if needed to stay below the maximum kWh usage limit and together with the kWh sensor, it calculates expected available power during charging time.

> [!IMPORTANT]
> If there is no kWh sensor for the heater, the calculation of needed power to reach normal operations after saving fails. The app still logs total consumption with your `power_consumption` sensor, but this does not take into account if the heater has been turned down for longer periods of time. This might affect calculated charging time.


### Temperatures
The climate is programmed to react to outdoor conditions configured with [Weather Sensors](https://github.com/Pythm/ad-ElectricalManagement?tab=readme-ov-file#weather-sensors). It's also recommended to use an additional indoor temperature sensor defined with `indoor_sensor_temp`. With that you can set a target, either with `target_indoor_temp` as an integer, or `target_indoor_input` as an Home Assistant input_number helper.

The `temperatures` dictionary consists of multiple temperature settings that adapt to the given `out`door temperature. Version 0.1.5 introduces additional ways to set climate temperatures.

Easiest way is to define `offset` +- degrees based on outside temperature. The offset also applies to `save_temp` and `away_temp`. Alternative to save temp you can define saving temperature with a `save_temp_offset` if you are using an input_number to set target.

```yaml
      save_temp_offset: -0.5
      away_temp: 13

      temperatures:
        - out: 3
          offset: 0.5
        - out: 7
          offset: 0
        - out: 10
          offset: -1
```

If you like to have more control over the save and away temperatures you can build your dictionary this way. This includes a `normal` operations temperature, an `away` setting for vacations, and a `save` mode for when electricity prices are high.

```yaml
      temperatures:
        - out: -4
          normal: 20
          save: 13
          away: 14
```

> [!TIP]
> To create a comprehensive temperature profile, start from your current indoor temperature and add a new dictionary entry for each additional degree adjustment required based on the outdoor temperature.

#### Savings Settings
Savings are calculated based on a future drop in price, with the given `pricedrop`, calculating backward from the price drop to save electricity as long as the price is higher than the low price + `pricedrop` + `pricedifference_increase` increase per hour backward. 1 is no increase so to get 7% increse in pricedifference pr hour configure with 1.07.

Configure `max_continuous_hours` for how long it can do savings. Defaults to 2 hours. Hot water boilers and heating cables in concrete are considered "magazines" and can be off for multiple hours before comfort is lost, so configure depending on the magazine for every climate/switch entity. You can also define a `on_for_minimum` that checks drop in price against low prices to ensure that it does not save for multiple hours without beeing able to heat up before prices goes up again.

#### Spending Settings
Spending hours occur before price increases and the temperature is increased to increase magazine energy. The amount per hour price increase to trigger this setting is defined with `priceincrease`. Additionally, `low_price_max_continuous_hours` defines how many hours before price increase the magazine needs to fill up with spend setting. If you are producing more electricity than you are consuming, the app will try to set spend settings on climate entities.

#### Vacation State
Turns down temperature to `away` setting. Uses the default vacation switch if left blank.

#### Window Sensors
The app will set the climate temperature to the `away` setting for as long as windows are open. It will also notify if the indoor temperature drops below the `normal` threshold. You can also specify a temperature threshold with `getting_cold` to only get notifications if a window is open and the temperature outside is below getting cold. This defaults to 18 degrees.

Define a window temperature sensor as `window_temp` to react quicker to sunny days and turn down heating before it gets to hot, with `window_offset` as an offset from target temperature. This is default to -3

#### Daylight Savings
The `daylight_savings` has a start and stop time. The time accepts the start time before midnight and the stop time after midnight. In addition, you can define presence so that it does not apply daylight savings.


#### Recipients
Define custom recipients per climate or use recipients defined in the main configuration.


#### Example configuration
Define either `name` of your heater, or input climate entity with `heater`.

```yaml
  climate:
    - name: floor_thermostat
    #- heater: climate.floor_thermostat
      consumptionSensor: sensor.floor_thermostat_electric_consumed_w_2
      kWhconsumptionSensor: sensor.floor_thermostat_electric_consumed_kwh_2
      max_continuous_hours: 2
      on_for_minimum: 12
      pricedrop: 1
      pricedifference_increase: 1.07

      #vacation: Will use apps default HA input boolean if not specified.
      automate: input_boolean.automate_heater
      #recipient: Define other recipients that configured in main configuration.

      indoor_sensor_temp: sensor.indoor_air_temperature
      window_temp: sensor.window_air_temperature
      window_offset: -3
      target_indoor_input: input_number.HA_input_number
      target_indoor_temp: 23

      save_temp_offset: -3
      save_temp: 12
      away_temp: 13

      rain_level: 3
      anemometer_speed: 40

      low_price_max_continuous_hours: 3
      priceincrease: 1

      windowsensors:
        - binary_sensor.your_window_door_is_open
      getting_cold: 20
      daytime_savings:
        - start: '07:30:00'
          stop: '22:00:00'
          presence:
            - person.wife
      temperatures:
        - out: 3
          offset: 0.5
        - out: 7
          offset: 0
```


## Switches
Hot-water boilers with no temperature sensors and only an on/off switch can also be controlled using the app's functionality. It will utilize ElectricityPrice functions to find optimal times for heating or turning on the heater. If a power consumption sensor is provided, it will enable more accurate calculations to avoid exceeding the maximum usage limit in ElectricalUsage.

Define either `name` of your heater, or input switch entity with `switch`

```yaml
  heater_switches:
    - name: hotwater
    #- switch: switch.hotwater
      consumptionSensor: sensor.hotwater_electric_consumption_w
      kWhconsumptionSensor: sensor.hotwater_electric_consumption_kWh
      max_continuous_hours: 8
      on_for_minimum: 8
      pricedrop: 0.3
      pricedifference_increase: 1.07

      #vacation: Will use apps default HA input boolean if not specified.
      automate: input_boolean.automate_heater
      #recipient: Define other recipients that configured in main configuration.
```


### Example App configuration

Putting it all together in a configuration with example names

```yaml
electricity:
  module: electricalManagement
  class: ElectricalUsage
  json_path: /conf/apps/ElectricalManagement/ElectricityData.json
  nordpool: sensor.nordpool_kwh_bergen_nok_3_10_025
  power_consumption: sensor.power_home
  accumulated_consumption_current_hour: sensor.accumulated_consumption_current_hour_home

  max_kwh_goal: 15 # 15 is default.
  buffer: 0.4 # 0.4 is default.

  daytax: 0.5648 # 0 is default
  nighttax: 0.4468 # 0 is default
  workday: binary_sensor.workday_sensor

  power_support_above: 0.9125 # Inkl vat
  support_amount: 0.9 # 90%

  startBeforePrice: 0.01
  stopAtPriceIncrease: 0.3

  vacation: input_boolean.vacation

  notify_receiver:
    - mobile_app_yourphone
    - mobile_app_yourotherphone
  infotext: input_text.information
  options:
    - pause_charging
    - notify_overconsumption

  outside_temperature: sensor.netatmo_out_temperature
  rain_sensor: sensor.netatmo_sensor_rain
  rain_level: 3
  anemometer: sensor.netatmo_anemometer_wind_strength
  anemometer_speed: 40

  # Cars and Chargers
  tesla:
    - charger: yourTesla
      pref_charge_limit: 90
      battery_size: 100
      finishByHour: input_number.finishChargingAt
      priority: 3
      charge_now: input_boolean.charge_Now
    - charger: yourOtherTesla
      pref_charge_limit: 70
      battery_size: 80
      finishByHour: input_number.yourOtherTesla_finishChargingAt
      priority: 4
      charge_now: input_boolean.yourOtherTesla_charge_Now

  easee:
    - charger: nameOfCharger
      charger_status: sensor.nameOfCharger_status
      reason_for_no_current: sensor.nameOfCharger_arsak_til_at_det_ikke_lades
      current: sensor.nameOfCharger_strom
      charger_power: sensor.nameOfCharger_effekt
      voltage: sensor.nameOfCharger_spenning
      max_charger_limit: sensor.nameOfCharger_maks_grense_for_lader
      session_energy: sensor.nameOfCharger_energi_ladesesjon
      idle_current: switch.nameOfCharger_ventestrom
      guest: input_boolean.easeelader_guest_using

  # Climate
  climate:
    - name: floor_thermostat
    #- heater: climate.floor_thermostat
      consumptionSensor: sensor.floor_thermostat_electric_consumed_w_2
      kWhconsumptionSensor: sensor.floor_thermostat_electric_consumed_kwh_2
      max_continuous_hours: 2
      on_for_minimum: 12
      pricedrop: 1
      pricedifference_increase: 1.07

      #vacation: Will use apps default HA input boolean if not specified.
      automate: input_boolean.automate_heater
      #recipient: Define other recipients that configured in main configuration.

      indoor_sensor_temp: sensor.indoor_air_temperature
      window_temp: sensor.window_air_temperature
      window_offset: -3
      target_indoor_input: input_number.HA_input_number
      target_indoor_temp: 23

      save_temp_offset: -3
      save_temp: 12
      away_temp: 13

      rain_level: 3
      anemometer_speed: 40

      low_price_max_continuous_hours: 3
      priceincrease: 1

      windowsensors:
        - binary_sensor.your_window_door_is_open
      getting_cold: 20
      daytime_savings:
        - start: '07:30:00'
          stop: '22:00:00'
          presence:
            - person.wife
      temperatures:
        - out: 3
          offset: 0.5
        - out: 7
          offset: 0

  heater_switches:
    - name: hotwater
    #- switch: switch.hotwater
      consumptionSensor: sensor.hotwater_electric_consumption_w
      kWhconsumptionSensor: sensor.hotwater_electric_consumption_kWh
      max_continuous_hours: 8
      on_for_minimum: 8
      pricedrop: 0.3
      pricedifference_increase: 1.07

      #vacation: Will use apps default HA input boolean if not specified.
      automate: input_boolean.automate_heater
      #recipient: Define other recipients that configured in main configuration.

  appliances:
    - remote_start: binary_sensor.oppvaskmaskin_remote_start
      day:
        program: switch.oppvaskmaskin_program_eco50
        running_time: 3
      night:
        program: switch.oppvaskmaskin_program_nightwash
        running_time: 4

```

key | optional | type | default | description
-- | -- | -- | -- | --
`module` | False | string | | The module name of the app.
`class` | False | string | | The name of the Class.
