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
