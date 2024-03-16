# ad-Electrical Management

> [!NOTE]
> This README is currently under construction. Some configurations have changed or will change to make them more understandable during this process. Recently, `peakdifference` changed to `pricedrop` in heater configuration. Similarly, `low_price_peakdifference` changed to `priceincrease`. Documentation on changes will only be given after the first release. Stay tuned!

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
> App is written to control electricity at your home location and will only change charging amps on chargers/cars that are home. If you want to control electricity usage in other places, I recommend creating a separate Home Assistant and AppDaemon instance for each location.

> [!TIP]
> I use sensors from Tibber Pulse connected to HAN port. Check out https://tibber.com/ If you are interested in switching to Tibber, you can use my invite link to get a startup bonus: https://invite.tibber.com/fydzcu9t

> [!NOTE]
> Max usage limit is developed according to the new calculation that Norwegian Energy providers base their grid tariffs on. We pay extra for the average of the 3 highest peak loads in steps of 2-5 kWh, 5-10 kWh, etc. This should be adaptable to other tariffs with some modifications.

> [!TIP]
> If you live in a country where there is no tariff on higher usage, set the limit to the same size as your main fuse in kWh.

If you have solar or other electricity production, add a production sensor and an accumulated production sensor. The app will try to charge any cars with surplus production. If all cars have reached their preferred charge limit, it will try to spend extra on heating. The calculations also support one consumption sensor with negative numbers for production. I do not have solar panels installed and this feature is only tested with manual input of test data. Please report any unexpected behavior.


### Dependencies:
To use this app, install the following components via HACS:
- Nordpool sensor: [Nordpool custom components](https://github.com/custom-components/nordpool)
- Workday sensor: [Home Assistant Workday integration](https://www.home-assistant.io/integrations/workday/)

The app uses the Met.no API for outside temperature if you do not configure an alternative source: [Met.no Home Assistant integration](https://www.home-assistant.io/integrations/met/)

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


### Json storage
Configure a path inclusive name 'name.json' to store a JSON file using the `json_path` as persistent data.

Persistent data will be updated with:
- The maximum kWh usage for the 3 highest hours.
- The maximum amperage that the car/charger can receive. This could occur when the set amperage in the charger is higher than what the car can receive, or if the charger starts low and increases output to perform a "soft start" charging.
- Store heater consumptions after saving functions with hours of savings and the heater + total power in watts after finishing charging, both with the outside temperature to better calculate how many hours cars need to finish charging.


### Other configurations for main app
Set a max kWh limit with `max_kwh_goal` and input your `buffer` to be on the safe side. Buffer size depends on how much of your electricity usage is controllable, and how strict you set your max kWh usage. It defaults to 0.4 as should be a good starting point.

> [!IMPORTANT]
> Max usage limit during one hour increases by 5 kWh if the average of the 3 highest consumption hours is over the limit. If the limit is set too low, it will turn down heating including switches and reduce charging to as low as 6 Ampere.

Add tax per kWh from your electricity grid provider with `daytax` and `nighttax`. Night tax applies from 22:00 to 06:00 and on weekends. The app will also look for 'binary_sensor.workday_sensor' and set night tax on holidays. If your [Workday Sensor](https://www.home-assistant.io/integrations/workday/) has another entity ID, you can configure it with `workday`.

In Norway, we get 90% electricity support (Strømstøtte) on electricity prices above 0.70 kr exclusive / 0.9125 kr inclusive VAT (MVA) calculated per hour. Define `power_support_above` and `support_amount` to have calculations take the support into account.

Set a main vacation switch with `away_state` to lower temperature when away. This can be configured/overridden individually per climate/switch entity if you are controlling apartments, etc.

Get notifications about charge time to your mobile phones with `notify_receiver`.

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
```

### Weather sensors
The app relies on outside temperature to log and calculate electricity usage. If no sensors is defined with `outside_temperature`, the app will try to retrieve data from [Met.no](https://www.home-assistant.io/integrations/met/).

In addition, you can configure rain and anemometer sensors. For more information on these sensors, check the climate documentation.

```yaml
  outside_temperature: sensor.netatmo_out_temperature
  rain_sensor: sensor.netatmo_regnsensor_rain
  anemometer: sensor.netatmo_anemometer_wind_strength
```


## Charging
Calculates the time required to charge electric vehicles based on the State of Charge (SOC), battery sizes, and charger data. The app also registers electricity usage from heaters and other appliances based on outside temperature to better calculate the time needed to charge if the maximum usage limit is expected to be below the necessary kW to maintain a full charge speed.

##### Priority settings for charger
Multiple cars with priority 1-5 are supported. The app queues the next car by priority to start when there is enough power available. If multiple chargers are in queue, charging vehicles will have to charge at full capacity before the next charger checks if it has 1.6kW of free capacity to start.

- Priority 1-2: These cars will start at the calculated time even if starting will result in heaters turning down/off to stay below the consumption limit. They will also charge until complete even when exceeding the time before a price increase, due to turning down the speed based on the consumption limit.

- Priority 3-5: These cars will wait to start charging until it is 1.6kW free capacity. They will also stop charging at the price increase after the calculated charge time ends..

> [!NOTE]
> If you have other high electricity consumption in combination with low limit, it will turn down charging but no lower than 6 Ampere. This may result in unfinished charging if the limit is too low or consumption is too high during calculated charge time.

Currently supports controlling Tesla vehicles and Easee wall chargers. Charging Tesla by controlling Easee charger is implemented but not yet tested. Documentation to request other cars or wall chargers will be published later.

### Tesla

> [!WARNING]
> It is nessesary to restart probably both Home Assistant and Appdaemon and in cases also reboot of vehicle to reestablish communications with Tesla API on occations. For now I have found it is needed after any service visits and also changes/reconfiguration of integration in Home Assistant. For instance if you need to update API key.

## Climate
entites are defined under `climate` and set the temperature based on the outside temperature. You configure it either by `name` or with the entity ID as `heater`, and the app will attempt to find consumption sensors or you can define current consumption using the `consumptionSensor` for the current consumption, and `kWhconsumptionSensor` for the total kWh that the climate has used. If the heater does not have a consumption sensor you can input its `power` in watts. The app uses this power to calculate how many heaters to turn down if needed to stay below the maximum kWh usage limit, and together with kWh sensor, it calculate expected available power during chargetime.

> [!IMPORTANT]
> If there is no kWh sensor for heater, the calculation of needed power to reach normal operations after saving fails. The app still logs total consumption with your power_consumption sensor, but this does not take into acount if the heater has been turned down for longer periodes of time. This might affect calculated chargetime.

### Savings settings
Savings is calculated based on a future drop in price, with the given `pricedrop`, calculating backward from price drop to save electricity as long as the price is higher than the low price + pricedrop + 5% increase per hour backwards. Configure `max_continuous_hours` for how long it can do savings. Defaults to 2 hours. Hot water boilers and heating cables in concrete are considered "magazines" and can be off for multiple hours before comfort is lost, so configure depending on the magazine for every climate/switch entity. You can also define a `on_for_minimum` for how many hours per day the entity needs to heat normally. This defaults to 12 hours.

### Spending settings
Spend hours occurs before price increases and the temperature is set to the `spend` setting to increse magazine energy. The amount per hour price increase to trigger this setting is defined with `priceincrease`. Additionally, `low_price_max_continuous_hours` defines how many hours before price increase the magazine needs to fill up with spend setting.


```yaml
  climate:
    - name: floor_thermostat
    #- heater: climate.floor_thermostat
    #  consumptionSensor: sensor.floor_thermostat_electric_consumed_w_2
    #  power: 1000
    #  kWhconsumptionSensor: sensor.floor_thermostat_electric_consumed_kwh_2
      max_continuous_hours: 2
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
        - out: 1
          normal: 19
          spend: 21
          save: 12
          away: 14
```


## Switches
Hot-water boilers with no temperature sensors with only a on/off switch. It will use functions from ElectricityPrice to find times to heat/turn on. If power consumption sensor is provided it will also be able to calculate better how to avoid max usage limit in ElectricalUsage.



### Example App configuration

```yaml
electricity:
  module: electricalManagement
  class: ElectricalUsage
  log_level: WARNING ### Set to INFO for more logging. 
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
    - charger: leia
      device_id: # 32 char ID string. Documentation to come
      reason_for_no_current: sensor.leia_arsak_til_at_det_ikke_lades
      current: sensor.leia_strom
      charger_power: sensor.leia_effekt
      voltage: sensor.leia_spenning
      max_charger_limit: sensor.leia_maks_grense_for_lader
      online_sensor: binary_sensor.leia_online
      session_energy: sensor.leia_energi_ladesesjon
      namespace: hass_leil
      finishByHour: input_number.easeelader_finishChargingAt
      priority: 2
      charge_now: input_boolean.easeelader_charge_Now
      guest: input_boolean.easeelader_guest_using

  heater_switches: # Used for dumb Hotwater boilers and other things to turn off during expencive hours
    - name: vvb_hus
#      switch: switch.vvb_hus
#      consumptionSensor: sensor.vvb_hus_electric_consumption_w
#      away_state: input_boolean.vekk_reist
      pricedrop: 0.12
      max_continuous_hours: 15
      on_for_minimum: 8
    - name: vvb_leilighet
      namespace: hass_leil
      away_state: input_boolean.leil_vekk_reist
      pricedrop: 0.12
      max_continuous_hours: 15
      on_for_minimum: 8

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
```

key | optional | type | default | description
-- | -- | -- | -- | --
`module` | False | string | | The module name of the app.
`class` | False | string | | The name of the Class.
