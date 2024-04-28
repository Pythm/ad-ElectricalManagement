# ad-Electrical Management

> [!NOTE]
> This README is currently under construction. Some configurations have changed or will change to make them more understandable during this process. Documentation on changes will only be given after the first release. Stay tuned!


The purpose of this app is to help reduce your electricity bill by:
- Automating charging times for electric vehicles (EVs), so they charge during off-peak hours when electricity rates are lower.
- Turning on/off heating sources and hot water boilers based on electricity prices.


## What platforms does it support?
This app is designed to work with [AppDaemon](https://github.com/AppDaemon/appdaemon) and [Home Assistant](https://www.home-assistant.io/).

Home Assistant is a popular open-source home automation platform that offers a wide range of features and integrations with various smart home devices. If you're not already using Home Assistant, I recommend checking it out.

AppDaemon is a loosely coupled, multi-threaded, sandboxed Python execution environment for writing automation apps for various types of home automation software, including Home Assistant and MQTT.


## How does it work?
The app calculates the optimal charging time for your electric car based on your historical electricity consumption, current rates, and future prices from the [Nordpool integration](https://github.com/custom-components/nordpool). It also takes into account other factors like weather conditions.

For heating sources and hot water boilers, the app uses similar calculations to determine when to turn them on/off based on the lowest possible electricity rates while still ensuring comfort needs are met. It can also turn up heating sources before a price increase. The app continuously monitors your energy consumption and adjusts settings accordingly, helping you avoid peak hours when electricity rates are higher and maximize savings during off-peak hours.

> [!TIP]
> I use sensors from Tibber Pulse connected to HAN port. Check out https://tibber.com/ If you are interested in changeing your electricity provider to Tibber, you can use my invite link to get a startup bonus: https://invite.tibber.com/fydzcu9t

> [!NOTE]
> Max usage limit is developed according to the new calculation that Norwegian Energy providers base their grid tariffs on. We pay extra for the average of the 3 highest peak loads in steps of 2-5 kWh, 5-10 kWh, etc. This should be adaptable to other tariffs with some modifications.

> [!TIP]
> If you live in a country where there is no tariff on higher usage, set the limit to the same size as your main fuse in kWh.

If you have solar or other electricity production, add a production sensor and an accumulated production sensor. The app will try to charge any cars with surplus production. If all cars have reached their preferred charge limit, it will try to spend extra on heating. The calculations also support one consumption sensor with negative numbers for production. I do not have solar panels installed and this feature is only tested with manual input of test data. Please report any unexpected behavior.


### Dependencies:
To use this app, install the following integrations:
From Home Assistant:
- Workday sensor: [Home Assistant Workday integration](https://www.home-assistant.io/integrations/workday/)

The app uses the Met.no API for outside temperature if you do not configure an alternative source: [Met.no Home Assistant integration](https://www.home-assistant.io/integrations/met/)

Install the following components via HACS:
- Nordpool sensor: [Nordpool custom components](https://github.com/custom-components/nordpool)


You only need the following optional components if they are configured in your setup:
- Tesla Custom Integration: [HACS Tesla integration](https://github.com/alandtse/tesla)
- Easee EV charger component for Home Assistant: [HACS Easee EV Charger integration](https://github.com/nordicopen/easee_hass)

## Installation
Download the `ElectricalManagement` directory from inside the `apps` directory here to your [Appdaemon](https://appdaemon.readthedocs.io/en/latest/) `apps` directory, then add configuration to a .yaml or .toml file to enable the `electricalManagement` module.

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
>  `accumulated_consumption_current_hour` is a kWh sensor that resets to 0 every hour


#### Json storage
Configure a path inclusive name 'name.json' to store a JSON file using the `json_path` as persistent data.

Persistent data will be updated with:
- The maximum kWh usage for the 3 highest hours.
- The maximum amperage that the car/charger can receive. This could occur when the set amperage in the charger is higher than what the car can receive, or if the charger starts low and increases output to perform a "soft start" charging.
- Store heater consumptions after saving functions with hours of savings and the heater + total power in watts after finishing charging, both with the outside temperature to better calculate how many hours cars need to finish charging.


#### Other configurations for main app
Set a maximum kWh limit using `max_kwh_goal` and define a `buffer`. Buffer size depends on how much of your electricity usage is controllable, and how strict you set your max kWh usage. It defaults to 0.4 as it should be a good starting point.

> [!IMPORTANT]
> The maximum usage limit per hour increases by 5 kWh if the average of the 3 highest consumption hours exceeds the limit. If the limit is set too low, it will reduce heating, turn off switches, and change charge current to as low as 6 Amperes.

Add tax per kWh from your electricity grid provider with `daytax` and `nighttax`. Night tax applies from 22:00 to 06:00 on workdays and all day on weekends. The app will also look for 'binary_sensor.workday_sensor' and set night tax on holidays. If your [Workday Sensor](https://www.home-assistant.io/integrations/workday/) has another entity ID, you can configure it with `workday`.

In Norway, we receive 90% electricity support (Strømstøtte) on electricity prices above 0.70 kr exclusive / 0.9125 kr inclusive VAT (MVA) calculated per hour. Define `power_support_above` and `support_amount` to have calculations take the support into account.

Set a main vacation switch with `away_state` to lower temperature when away. This can be configured/overridden individually for each climate/switch entity if you are controlling multiple apartments, etc.

Receive notifications about charge time to your devices with `notify_receiver`. It will also notify if you left a window open and it is getting cold, or if it is getting quite hot and the window is closed.

```yaml
  max_kwh_goal: 5 # 5 is default.
  buffer: 0.4
  daytax: 0.5573 # 0 is default
  nighttax: 0.4393 # 0 is default
  workday: binary_sensor.workday_sensor
  power_support_above: 0.9125 # Inkl vat
  support_amount: 0.9 # 90%
  away_state: input_boolean.vacation
  notify_receiver:
    - mobile_app_yourphone
    - mobile_app_yourotherphone
```


### Weather Sensors
The app relies on the outside temperature to log and calculate electricity usage. If no `outside_temperature` sensor is defined, the app will attempt to retrieve data from the [Met.no](https://www.home-assistant.io/integrations/met/) integration. Climate entities set heating based on the outside temperature.

In addition, you can configure rain and anemometer sensors. These are used by climate entities where you can define a rain amount and wind speed to increase heating by 1 degree.

```yaml
  outside_temperature: sensor.netatmo_out_temperature
  rain_sensor: sensor.netatmo_regnsensor_rain
  anemometer: sensor.netatmo_anemometer_wind_strength
```


### Namespace
A key feature of Appdaemon is the ability to define custom namespaces. Visit the [Appdaemon documentation](https://appdaemon.readthedocs.io/en/latest/CONFIGURE.html#) for more information.

If you have not configured any namespace in your 'appdaemon.yaml' file, you can skip this section.

> [!IMPORTANT]
> You must configure the `namespace` in every charger and climate that belongs to Home Assistant instances with a custom namespace.

> :bulb: **TIP**
> The app is designed to control electricity usage at your primary residence and will only adjust charging amps on chargers/cars that are  within your home location. If you want to manage electricity consumption in other locations, I recommend setting up a separate Home Assistant and AppDaemon instance for each location.


### Charging
The app calculates the time required to charge electric vehicles based on State of Charge (SOC), battery sizes, and charger data. It also takes outside temperature into account to better estimate the time needed for charging if the maximum usage limit is expected to be below the necessary kW to maintain full charge speed.

> [!NOTE]
> If you have other high electricity consumption in combination with a low limit, it will reduce charging but not lower than 6 Ampere. This may result in unfinished charging if the limit is too low or consumption is too high during calculated charge time.

Currently supports controlling Tesla vehicles and Easee wall chargers. Charging Tesla by controlling an Easee charger is implemented but not yet tested. Documentation to request other cars or wall chargers will be published later.

### Configuration for all vehicles and wall chargers
#### Priority settings for charger
Multiple cars with priority 1-5 are supported. The app queues the next car by priority when there is enough power available. If multiple chargers are in queue, charging vehicles will have to charge at full capacity before the next charger checks if it has 1.6kW of free capacity to start.

- Priority 1-2: These cars will start at the calculated time even if starting will result in heaters turning down/off to stay below the consumption limit. They will also charge until complete even when exceeding the time before a price increase, due to turning down the speed based on the consumption limit.

- Priority 3-5: These cars will wait to start charging until it is 1.6kW free capacity. They will also stop charging at the price increase after the calculated charge time ends.

#### Home Assistant helpers
Create helpers in Home Assistant to manage charging.

Charging is by default finished by 7:00. To set a different hour the charging should be finished use an `input_number` sensor configured with `finishByHour`.

To bypass smart charging and charge now, you can configure `charge_now` with an `input_boolean` and set it to true. The sensor will be set to false after charging is finished or the charger is disconnected.

If you are generating electricity, you can choose to charge only during surplus production. Define another `input_boolean` and configure it with the `charge_on_solar` option.

### Tesla

> :warning: **WARNING**
> It is necessary to restart probably both Home Assistant and Appdaemon, and in some cases, reboot the vehicle to re-establish communications with the Tesla API after any service visits and also changes/reconfiguration of the integration in Home Assistant, such as if you need to update the API key.

Input the name of your Tesla with `charger`. Check logs for any errors.

```yaml
  tesla:
    - charger: nameOfCar
      pref_charge_limit: 90
      battery_size: 100
```

### Easee
The Easee integration automatically detects the wall charger's information if its sensor names are in English; simply provide the name of your Easee with the `charger` option. However, if your sensors have names in another language, you must manually input the correct sensor names in the configuration.

```yaml
  easee:
    - charger: nameOfCharger
      charger_status: sensor.nameOfCharger_status
      reason_for_no_current: sensor.nameOfCharger_arsak_til_at_det_ikke_lades
      current: sensor.nameOfCharger_strom
      charger_power: sensor.nameOfCharger_effekt
      voltage: sensor.nameOfCharger_spenning
      max_charger_limit: sensor.nameOfCharger_maks_grense_for_lader
      online_sensor: binary_sensor.nameOfCharger_online
      session_energy: sensor.nameOfCharger_energi_ladesesjon
```

The app stores the highest session energy in persistent storage, as this is the only indication of how much charge is needed to reach full capacity. It then calculates the time required for charging based on this information.

If another vehicle is using the charger, you can disable logging of the highest session energy and maximum ampere that the guest vehicle can charge by using an Home Assistant input_boolean helper configured as `guest`. Enabling this sensor will initiate the charging session immediately.


Apologies for that. Here's an updated version of the text with some improvements and corrections in punctuation, capitalization, and grammar:

## Climate
Here you configure climate entities that you want to control based on outside temperature and electricity price.

> For HVAC and other climate entities that are not very power consuming, you should check out [Climate Commander](https://github.com/Pythm/ad-ClimateCommander). That app is based around the same logic with the outside temperature, but more automated to keep a constant inside temperature.

Climate entities are defined under `climate` and set the temperature based on the outside temperature. You configure it either by `name` or with the entity ID as `heater`, and the app will attempt to find consumption sensors based on some default zwave naming, or you can define current consumption using the `consumptionSensor` for the current consumption, and `kWhconsumptionSensor` for the total kWh that the climate has used.

> [!IMPORTANT]
> If no `consumptionSensor` or `kWhconsumptionSensor` is found or configured, the app will log with a warning to your AppDaemon log, or the log you define in the app configuration.

If the heater does not have a consumption sensor, you can input its `power` in watts. The app uses this power to calculate how many heaters to turn down if needed to stay below the maximum kWh usage limit and together with the kWh sensor, it calculates expected available power during charging time.

> [!IMPORTANT]
> If there is no kWh sensor for the heater, the calculation of needed power to reach normal operations after saving fails. The app still logs total consumption with your `power_consumption` sensor, but this does not take into account if the heater has been turned down for longer periods of time. This might affect calculated charging time.

### Temperatures
You define the climate working temperatures based on outdoor conditions. The `temperatures` dictionary consists of multiple temperature settings that adapt to the given `out`door temperature. It includes a `normal` operations temperature, an `away` setting for vacations, and a `save` mode for when electricity prices are high. Optionally, you can also specify a `spend` mode temperature.

```yaml
      temperatures:
        - out: -4
          normal: 20
          spend: 21
          save: 13
          away: 14
```

> [!TIP]
> To create a comprehensive temperature profile, start from your current indoor temperature and add a new dictionary entry for each additional degree adjustment required based on the outdoor temperature.

#### Savings Settings
Savings are calculated based on a future drop in price, with the given `pricedrop`, calculating backward from the price drop to save electricity as long as the price is higher than the low price + `pricedrop` + 5% increase per hour backward. Configure `max_continuous_hours` for how long it can do savings. Defaults to 2 hours. Hot water boilers and heating cables in concrete are considered "magazines" and can be off for multiple hours before comfort is lost, so configure depending on the magazine for every climate/switch entity. You also define a `on_for_minimum` for how many hours per day the entity needs to heat normally. This defaults to 12 hours.

#### Spending Settings
Spending hours occur before price increases and the temperature is set to the `spend` setting to increase magazine energy. The amount per hour price increase to trigger this setting is defined with `priceincrease`. Additionally, `low_price_max_continuous_hours` defines how many hours before price increase the magazine needs to fill up with spend setting. If you are producing more electricity than you are consuming, the app will try to set spend settings on climate entities.

Hi! Here is the corrected and improved text:

#### Away State
Turns down temperature to `away` setting. Uses the default away switch if left blank.

#### Breaking Automation
The climate will automate by default but you can define a Home Assistant `input_boolean` helper to turn it off. Note that when the switch is on, it will automate.

#### Indoor Temperature
It's recommended to use an additional indoor temperature sensor defined with `indoor_sensor_temp`. Set a target with `target_indoor_temp`, and the app will reduce heating if exceeded.

#### Window Sensors
The app will set the climate temperature to the `away` setting for as long as windows are open. It will also notify if the indoor temperature drops below the `normal` threshold.

#### Daylight Savings
The `daylight_savings` has a start and stop time. The time accepts the start time before midnight and the stop time after midnight. In addition, you can define presence so that it does not apply daylight savings.

#### Recipients
Define custom recipients per climate or use recipients defined in the main configuration.


```yaml
  climate:
    - name: floor_thermostat
    #- heater: climate.floor_thermostat
    #  consumptionSensor: sensor.floor_thermostat_electric_consumed_w_2
    #  power: 300
    #  kWhconsumptionSensor: sensor.floor_thermostat_electric_consumed_kwh_2
      max_continuous_hours: 2
      on_for_minimum: 12
      pricedrop: 0.15
      low_price_max_continuous_hours: 3
      priceincrease: 0.65
      #away_state: Will use default if not specified.
      automate: input_boolean.automate_heating
      indoor_sensor_temp: sensor.bod_fryseskap_air_temperature
      target_indoor_temp: 20
      windowsensors:
        - binary_sensor.your_window_door_is_open
      daytime_savings:
        - start: '07:30:00'
          stop: '22:00:00'
          presence:
            - person.wife
      #recipient:
      temperatures:
        - out: -4
          normal: 20
          spend: 21
          save: 13
          away: 14
        - out: 1
          normal: 19
          spend: 21
          save: 12
          away: 14
```


## Switches
Hot-water boilers with no temperature sensors and only an on/off switch can also be controlled using the app's functionality. It will utilize ElectricityPrice functions to find optimal times for heating or turning on the heater. If a power consumption sensor is provided, it will enable more accurate calculations to avoid exceeding the maximum usage limit in ElectricalUsage.

```yaml
  heater_switches:
    - name: hotwater
    #- switch: switch.hotwater
    #  consumptionSensor: sensor.hotwater_electric_consumption_w
    #  away_state: input_boolean.vacation
      pricedrop: 0.3
      max_continuous_hours: 8
      on_for_minimum: 8
```


### Example App configuration

Putting it all together in a configuration with example names

```yaml
electricity:
  module: electricalManagement
  class: ElectricalUsage
  json_path: /conf/apps/ElectricalManagement/ElectricityData.json
  nordpool: sensor.nordpool_kwh_bergen_nok_3_10_025
  daytax: 0.4738
  nighttax: 0.3558
  power_support_above: 0.9125 # Inkl vat
  support_amount: 0.9 # 90%
  workday: binary_sensor.workday_sensor
  power_consumption: sensor.power_hjemme
  accumulated_consumption_current_hour: sensor.accumulated_consumption_current_hour_hjemme
  max_kwh_goal: 10
  buffer: 0.3
  outside_temperature: sensor.netatmo_out_temperature
  rain_sensor: sensor.netatmo_regnsensor_rain
  anemometer: sensor.netatmo_anemometer_wind_strength
  away_state: input_boolean.vacation
  notify_receiver:
    - mobile_app_your_phone

  tesla:
    - charger: yourTesla
      pref_charge_limit: 90
      battery_size: 100
      finishByHour: input_number.finishChargingAt
      priority: 3
      charge_now: input_boolean.charge_Now
      electric_consumption: sensor.tesla_electric_consumption_w
      departure: input_datetime.departure # Not implemented yet
    - charger: yourOtherTesla
      pref_charge_limit: 70
      battery_size: 80
      finishByHour: input_number.yourOtherTesla_finishChargingAt
      priority: 4
      charge_now: input_boolean.yourOtherTesla_charge_Now
      electric_consumption: sensor.tesla_electric_consumption_w
      departure: input_datetime.departure_yourOtherTesla

  easee:
    - charger: nameOfCharger
      charger_status: sensor.nameOfCharger_status
      reason_for_no_current: sensor.nameOfCharger_arsak_til_at_det_ikke_lades
      current: sensor.nameOfCharger_strom
      charger_power: sensor.nameOfCharger_effekt
      voltage: sensor.nameOfCharger_spenning
      max_charger_limit: sensor.nameOfCharger_maks_grense_for_lader
      online_sensor: binary_sensor.nameOfCharger_online
      session_energy: sensor.nameOfCharger_energi_ladesesjon
      namespace: hass_leil
      finishByHour: input_number.easeelader_finishChargingAt
      priority: 2
      charge_now: input_boolean.easeelader_charge_Now
      guest: input_boolean.easeelader_guest_using

  climate:
    - name: floor_thermostat
    #- heater: climate.floor_thermostat
    #  consumptionSensor: sensor.floor_thermostat_electric_consumed_w_2
    #  kWhconsumptionSensor: sensor.floor_thermostat_electric_consumed_kwh_2
      max_continuous_hours: 14
      on_for_minimum: 6
      pricedrop: 0.15
      #away_state: Will use default if not specified.
      automate: input_boolean.automatiser_varmekabler_bod
      #recipient:
      indoor_sensor_temp: sensor.bod_fryseskap_air_temperature
      target_indoor_temp: 20
      low_price_max_continuous_hours: 3
      priceincrease: 0.65
      temperatures:
        - out: -4
          normal: 20
          spend: 21
          save: 13
          away: 14
        - out: -3
          normal: 19
          spend: 21
          save: 12
          away: 14
        - out: 2
          normal: 18
          spend: 20
          save: 11
          away: 13
        - out: 7
          normal: 16
          spend: 18
          save: 11
          away: 13
        - out: 11
          normal: 15
          save: 10
          away: 12
        - out: 14
          normal: 13
          save: 10
          away: 12
        - out: 18
          normal: 12
          save: 10
          away: 12

  heater_switches:
    - name: hotwater
    #- switch: switch.hotwater
    #  consumptionSensor: sensor.hotwater_electric_consumption_w
    #  away_state: input_boolean.vacation
      pricedrop: 0.3
      max_continuous_hours: 8
      on_for_minimum: 8

  appliances:
    - remote_start: binary_sensor.oppvaskmaskin_remote_start
      program: switch.oppvaskmaskin_program_nightwash
      running_time: 4
      finishByHour: 6

```

key | optional | type | default | description
-- | -- | -- | -- | --
`module` | False | string | | The module name of the app.
`class` | False | string | | The name of the Class.
