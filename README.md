
# ad-Electrical Management

The purpose of this Appdaemon app is to help reduce your electricity bill by:

- Automating charging times for electric vehicles (EVs), so they charge when electricity rates are lower.
- Turning up/down heating sources and on/off hot water boilers based on electricity prices.
- Stay within hourly max kWh consumption based on grid tariffs if applicable.

---

## 🚨 Breaking Changes

### **1.0.0** - A complete rewrite
Please update all configurations in the breaking change upgrading to version 1.0.0 or later from earlier versions.

- **Calculation of electricityprice**
As of release 1.0.0, the calculations for electricity prices are now handled by another app. Please install the [ElectricalPriceCalc](https://github.com/Pythm/ElectricalPriceCalc) version 0.1.6 or later. Configure the ElectricalPriceCalc app and add it to `ElectricalManagement` with `electricalPriceApp` like this:

```yaml
electricalPriceApp: electricalPriceCalc # name of the app
```

The separation is to make it easier to maintain and continue development and creates posibilities to use the price calculations in other apps as well. There are some big changes under the hood, such as support for Nordpool prices every 15 minutes, and datetime objects are now timezone aware.

- **Spelling Correction**
Changed `notify_reciever` → `notify_receiver`.

- **Json Storage**
A json file will be created in `{self.AD.config_dir}/persistent/electricity/` or your defined location using the `json_path` in configuration.
The persistent data will be updated with key data and configuration of your entities.

Please use a new location or backup and delete your old json file upgrading from pre 1.0 or a beta version.

- **Definig climate and heater switches**
Defining climate and heater_switches can now only be done with the entity, not a name. Configuration with name could potentially find wrong sensors.

- **Weather sensors**
Electrical Management app is fully relying on the [ad‑Weather](https://github.com/Pythm/ad-Weather) app for weather data. No configuration for weather sensors in app anymore.
---

## 🧭 Planned Changes

I plan to make changes to integrate [ClimateCommander](https://github.com/Pythm/ad-ClimateCommander) with ElectricalManagement.

---

## 📱 Supported Platforms

This app is designed to work with:

- [AppDaemon](https://github.com/AppDaemon/appdaemon)
- [Home Assistant](https://www.home-assistant.io/)

Home Assistant is a popular open-source home automation platform that offers a wide range of features and integrations with various smart home devices. If you're not already using Home Assistant, I recommend checking it out.

AppDaemon is a loosely coupled, multi-threaded, sandboxed Python execution environment for writing automation apps for various types of home automation software, including Home Assistant and MQTT.

---

## 📊 How Does It Work?

The app calculates the optimal charging time for your electric car based on your historical electricity consumption. It considers factors like weather conditions, current rates, and future prices.

For heating sources and hot water boilers, the app uses similar calculations to determine when to turn them on/off/up/down based on the lowest possible electricity rates, while still ensuring comfort needs are met.

The app can also continuously monitors your energy consumption and adjusts accordingly, helping you stay within grid tariffs.

---

### 🔔 Tips & Notes

> [!TIP]  
> I use sensors from Tibber Pulse connected to HAN port. Check out https://tibber.com/ If you're interested in changing your electricity provider to Tibber, you can ask me for a invite link to get a startup bonus.

> [!NOTE]  
> Max usage limit `max_kwh_goal` is developed according to the calculation that Norwegian Energy providers base their grid tariffs on. We pay extra for the average of the 3 highest peak loads in steps of 5-10 kWh, 10-15 kWh, etc. This should be adaptable to other tariffs with some modifications.

If you have solar or other electricity production, add a production sensor and an accumulated production sensor. The app will try to charge any cars with surplus production. If all cars have reached their preferred charge limit, it will try to spend extra on heating. The calculations also support one consumption sensor with negative numbers for production.

> [!NOTE]  
> I do not have solar panels installed and this feature is only tested with manual input of test data. Please consider this untested and report any unexpected behavior.

---

## 📦 Dependencies

You'll need to clone and configure:
- [ElectricalPriceCalc](https://github.com/Pythm/ElectricalPriceCalc)
- [ad‑Weather](https://github.com/Pythm/ad-Weather)

You only need the following optional components if they are configured in your setup. Currently supported directly in app:

- Tesla Custom Integration: [HACS Tesla integration](https://github.com/alandtse/tesla)
- Easee EV charger component for Home Assistant: [HACS Easee EV Charger integration](https://github.com/nordicopen/easee_hass)

---

## 📦 Installation and Configuration

1. `git clone` into your [AppDaemon](https://appdaemon.readthedocs.io/en/latest/) `apps` directory.
2. Add configuration to a `.yaml` or `.toml` file to enable the `ElectricalManagement` module.

Example startup configuration with suggested values:

```yaml
electricity:
  module: electricalManagement
  class: ElectricalUsage
  electricalPriceApp: electricalPriceCalc # Your ElectricalPriceCalc app
```

---

### 🗂️ Json Storage

A json file will be created in `{self.AD.config_dir}/persistent/electricity/` or your defined location using the `json_path` in configuration.
The persistent data will be updated with key data and configuration of your entities.

> [!TIP]  
> You can check the json file for automatically found sensors for cars, chargers and heaters. Remember that the json is only written to during reboot and at 14.30.
---

### 🔌 Grid tariffs

Provide a consumption sensor `power_consumption` and an accumulated consumption per hour sensor `accumulated_consumption_current_hour` to calculate and adjust electricity usage.

> [!IMPORTANT]  
> `accumulated_consumption_current_hour` is a kWh sensor that resets to zero every hour.

Set a maximum kWh limit using `max_kwh_goal` and define a `buffer`. Buffer size depends on how much of your electricity usage is controllable, and how strict you set your max kWh usage. It defaults to 0.4 as it should be a good starting point. The top three hours is stored under `topUsage` in the json file.

> [!IMPORTANT]  
> The maximum usage limit per hour, `max_kwh_goal`, is by default 15 kWh. If the average of the 3 highest consumption hours exceeds this limit, it will increase by 5 kWh. If the limit is set too low, it may reduce heating, turn off switches, and change the charge current. Please define a proper value for `max_kwh_goal` according to your normal electricity usage.

```yaml
  power_consumption: sensor.power_home
  accumulated_consumption_current_hour: sensor.accumulated_consumption_current_hour_home
  max_kwh_goal: 15
  buffer: 0.4
```

### 🏖️ Setting Vacation Mode

Set a main `vacation` switch to lower temperature when away. This can be configured/overridden individually for each climate/switch entity if you are controlling multiple apartments, etc.

```yaml
  vacation: input_boolean.vacation
```

---

### 📌 Defining options

#### 🔌 Reducing Consumption to Stay Below Max kWh Goal

The app checks power consumptions and reacts to prevent using more than defined with `max_kwh_goal`. It reduces charging speed on car(s) currently charging to a minimum, before turning down heater_switches and climate entities. If it is still going over the app can pause charging if `pause_charging` is configured under `options`.


#### 📢 Notifications and Information

Receive notifications about charge time to your devices with `notify_receiver`. It will also notify if you left a window open and it is getting cold, or if it is getting quite hot and the window is closed if you configure windows with heaters.

You can also create and configure an Home Assistant input_text with `infotext` to display currently planned charge time in Home Assistant or some external displays.

Running multiple ElectricalManagement instances? Configure a home_name so you know where the notifications comes from.

```yaml
  notify_receiver:
    - mobile_app_yourphone
    - mobile_app_yourotherphone
  home_name: home
  infotext: input_text.information
  options:
    - notify_overconsumption
    - pause_charging
```

You can also configure `electricalManagement` to use your own Notification app instead with `notify_app`. You'll need to have a function in your app to receive. App sends one call with kwargs: message, message_title, message_recipient, and also_if_not_home. Data might contain tag to replace old notifications. More kwargs can be added later or on request.

```python
def send_notification(self, **kwargs) -> None:
    """Sends notification to recipients via Home Assistant notification.
    """
    message: str = kwargs['message']
    message_title: str = kwargs.get('message_title', 'Home Assistant')
    message_recipient: str = kwargs.get('message_recipient', True)
    also_if_not_home: bool = kwargs.get('also_if_not_home', False)
    data: dict = kwargs.get('data', {})
    for re in message_recipient:
        self.ADapi.call_service(f'notify/{re}',
            title = message_title,
            message = message,
            data = data,
            namespace = self.namespace
        )
```

---

### 🌤️ Weather Sensors

The app is relying on the [ad‑Weather](https://github.com/Pythm/ad-Weather) app for weather data:

- It consolidates all your weather sensors into a single app.  
- It **publishes events** that other apps (like ElectricalManagement) can use.

ElectricalManagement relies on the outside temperature to log and calculate electricity usage. Climate entities set heating based on the outside temperature.

In addition you can define a rain amount `rain_level` (Defaults to 3) and wind speed `anemometer_speed` (Defaults to 40) to increase heating in the climate entities.

---

### 🧭 Namespace

A key feature of Appdaemon is the ability to define custom namespaces. Visit the [Appdaemon documentation](https://appdaemon.readthedocs.io/en/latest/CONFIGURE.html#) for more information.

If you have not configured any namespace for your HASS plugin in your `appdaemon.yaml` file, you can safely ignore namespaces.

> [!IMPORTANT]  
> As of version 0.1.5 you can set a namespace for heater/climate and charging entities with `main_namespace` if you have defined a custom HASS namespace. You can then configure the `namespace` in every charger and heater/climate that belongs to Home Assistant instances with another custom namespace if you are running multiple Home Assistant instances at home.

> :bulb: **TIP**  
> The app is designed to control electricity usage at your primary residence and will only adjust charging amps on chargers/cars that are within your home location. If you want to manage electricity consumption in other locations, I recommend setting up a separate Home Assistant and AppDaemon instance for each location.

---

### 🔄 Mode Change Events

This app listens to event `"MODE_CHANGE"` in Home Assistant. It reacts to mode `"fire"` by turning off all heaters and stopping charging, and `"false-alarm"` to revert back to normal operations.

> The use of events in Appdaemon and Home Assistant is well documented in [Appdaemon docs - Events](https://appdaemon.readthedocs.io/en/latest/APPGUIDE.html#events)

To set mode from another Appdaemon app simply use:

```python
self.fire_event("MODE_CHANGE", mode = 'your_mode_name')
```

> [!TIP]  
> `ElectricalManagement` now supports the same translation on Modes as Lightwand. Check out the documentation for Lightwand in the [translation section](https://github.com/Pythm/ad-Lightwand?tab=readme-ov-file#translating-or-changing-modes) to listen for another event than `"MODE_CHANGE"` or use your own names for the pre defined mode names and change `"fire"` and `"false-alarm"` to comply with rest of your smart home. To adopt the translation one lightwand app needs to be configured with the translation configuration.

---


## 🔋 Charging

The app calculates electric vehicle (EV) charging time based on the State of Charge (SOC), battery size, and outside temperature.

> [!NOTE]  
> If you have other high electricity consumption in combination with a low limit, it may reduce charging down to the minimum allowed by the charger, or even stop charging if you configure with option `pause_charging`. This could result in unfinished charging if the limit is too low or consumption is too high during the calculated charge time.

The app supports controlling Tesla vehicles directly and via Easee wall chargers. There is also a possibility to configure your car with sensors.

---

#### 🔌 Customizing strict charging

The app calculates the optimal charging price and schedule based on data from [ElectricalPriceCalc](https://github.com/Pythm/ElectricalPriceCalc), ensuring a coherent time frame from start to finish. Vehicles will charge when the price is cheaper than the calculated rate. Additionally, you can customize the charging behavior by specifying a price difference between the calculated charging period with `startBeforePrice` (default 0.01) to start earlier if prices are still low, ensuring enough time to charge even with limited data for maximum kWh usage per hour. You can also force stop charging with `stopAtPriceIncrease` (default 0.3) if the charging isn't completed within the calculated time.

```yaml
  startBeforePrice: 0.01
  stopAtPriceIncrease: 0.3
```
---

### 🛠️ Configuration for Chargers

#### Home Assistant Helpers

Create Home Assistant helpers to manage charging:

1. **Guest Function**: There is a `guest` function defined with an `input_boolean` on chargers. This allows guests to charge. You will need to set up a phone to receive nofication and long press the received notification to select either charge now, or input estimated kWh to charge.

---

### 🚘 Easee Chargers

> [!IMPORTANT]  
> Locking Easee charger to 3-phase IT net (230V) requires a minimum of 11A to charge. The app can turn down charging as low as 6A, and if so, the charging will stop. To avoid this, set Phase mode to Automatic in your Easee app.

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

---

### 🚗 Configuration for Cars

#### Home Assistant Helpers

Create Home Assistant helpers to manage charging:

1. **Default Finish Time**: Charging is completed by default at 7:00 AM. To set a different hour for the charging to be finished, use an `input_number` sensor configured with `finishByHour`. This can also be configured with an integer directly in configuration if you do not intend to change it.
2. **Charge Now**: To bypass smart charging and charge immediately, configure `charge_now` as an `input_boolean`. The sensor will be automatically set to false after charging is completed or when the charger is disconnected.
3. **Charging on Solar Power**: If you generate electricity, you can choose to charge only during surplus production. Define another `input_boolean` and configure it with the `charge_on_solar` option.

#### Priority Settings for Cars

Multiple cars with priority levels 1–5 are supported by the app. The app queues the next car based on priority when there's enough power available. If multiple vehicles are in the queue, charging vehicles must reach full capacity or a minimum of 16A before the next charger starts.

Priority settings for cars include:

- Priority 1–2: These cars will continue charging until complete, regardless of any price increases due to adjusting the speed based on the consumption limit.
- Priority 3–5: These cars will stop charging at the price increase that occurs after the calculated charge time ends.

#### Battery Size
The app will calculate battery size. It defaults to 100kWh to start with. If you have a car with both `battery_sensor` that gives you SOC and a `session_energy` either from onboard charger or wall charger it will calculate your size. It needs to charge over 35% to store your size.

You can configure `battery_size` for your car, but then the calculations will be reset to that value on every restart.

> [!TIP]
> Configure the battery_size once and it will be stored to persistent data (json) and remove it so calculations can overwrite the value.

---

### 🚘 Configuring Teslas

> :warning: **WARNING**  
> It is necessary to restart the Tesla integration, and in some cases, reboot the vehicle to re-establish communications with the Tesla API after any service visits and also changes/reconfiguration of the integration in Home Assistant, such as if you need to update the API key.

Input the name of your Tesla using the `car` option. Check logs for any errors and provide missing sensors. The app will automatically find all sensors listed in the cars section below.

```yaml
  tesla:
    - car: nameOfCar
      pref_charge_limit: 90
      finishByHour: input_number.charge_finished
      priority: 3
      charge_now: input_boolean.charge_now
```

---

### 🚗 Defining Other Vehicles to Charge

Please try configure your other cars with the following sensors under `cars`:

```yaml
  cars:
    - carName: name-your-car
      charger_sensor: binary_sensor._charger
      charge_limit: number._charge_limit
      battery_sensor: sensor._battery
      asleep_sensor: binary_sensor._asleep
      online_sensor: binary_sensor._online
      location_tracker: device_tracker._location_tracker
      destination_location_tracker: device_tracker._destination_location_tracker
      arrival_time: sensor._arrival_time
      software_update: update._software_update
      force_data_update: button._force_data_update
      polling_switch: switch._polling
      data_last_update_time: sensor._data_last_update_time

      charger_switch: switch._charger
      charging_amps: number._charging_amps
      charger_power: sensor._charger_power
      session_energy: sensor._energy_added
      maxChargerAmpere: 32
      volts: 220
      phases: 3

```

In addition to the HA helpers.

> [!WARNING]  
> The default location if no sensor for location is provided, is 'home'. This will stop charging if you are controlling your car and not a wall charger if it is not charge time. Please make sure your location sensor is functioning properly. If your car does not provide a location you can use another tracker, or make sure you have configured a `charge_now` to force charging when not home.

If the sensors does not work for your car, please open an issue.

---

## 🌡️ Climate

Here you configure climate entities that you want to control based on outside temperature and electricity price.

> For HVAC and other climate entities that are not very power consuming, you should check out [Climate Commander](https://github.com/Pythm/ad-ClimateCommander). That app is written to keep a constant inside temperature.

Climate entities are defined under `climate` and you configure the temperature it should heat to, based on the outside temperature. You configure it either by `name` or with the entity ID as `heater`, and the app will attempt to find consumption sensors based on some default zwave naming, or you can define current consumption using the `consumptionSensor` for the current consumption, and `kWhconsumptionSensor` for the total kWh that the climate has used.

If the heater does not have a consumption sensor, you can input its `power` in watts. The app uses this power to calculate how many heaters to turn down if needed to stay below the maximum kWh usage limit and together with the kWh sensor, it calculates expected available power during charging time.

> [!IMPORTANT]  
> If there is no kWh sensor for the heater, the calculation of needed power to reach normal operations after saving fails. The app still logs total consumption with your `power_consumption` sensor, but this does not take into account if the heater has been turned down for longer periods of time. This might affect calculated charging time.

---

### 🌡️ Temperatures

The climate is programmed to react to outdoor conditions configured with [Weather Sensors](https://github.com/Pythm/ad-ElectricalManagement?tab=readme-ov-file#weather-sensors). It's also recommended to use an additional indoor temperature sensor defined with `indoor_sensor_temp`. With that you can set a target, either with `target_indoor_temp` as an integer, or `target_indoor_input` as an Home Assistant input_number helper.

The `temperatures` dictionary consists of multiple temperature settings that adapt to the given `out`door temperature. Version 0.1.5 introduces additional ways to set climate temperatures.

Easiest way is to define `offset` ± degrees based on outside temperature. The offset also applies to `save_temp` and `away_temp`. Alternative to save temp you can define saving temperature with a `save_temp_offset` if you are using an input_number to set target.

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

---

### 💡 Savings Settings

Savings are calculated based on a future drop in price, with the given `pricedrop`, calculating backward from the price drop to save electricity as long as the price is higher than the low price + `pricedrop` + `pricedifference_increase` increase per hour backward. 1 is no increase so to get 7% increase in pricedifference per hour configure with 1.07.

Configure `max_continuous_hours` for how long it can do savings. Defaults to 2 hours. Hot water boilers and heating cables in concrete are considered "magazines" and can be off for multiple hours before comfort is lost, so configure depending on the magazine for every climate/switch entity. You can also define a `on_for_minimum` that checks drop in price against low prices to ensure that it does not save for multiple hours without being able to heat up before prices go up again.

The app will log any saving hours when you change the any of the above. You can also configure with option `print_save_hours` in heater to print every day.
```yaml
      options:
        - print_save_hours
```
---

### 💸 Spending Settings

Spending hours occur before price increases and the temperature is increased to increase magazine energy. The amount per hour price increase to trigger this setting is defined with `priceincrease`. Additionally, `low_price_max_continuous_hours` defines how many hours before price increase the magazine needs to fill up with spend setting. If you are producing more electricity than you are consuming, the app will try to set spend settings on climate entities.

---

### 🏖️ Vacation State

Turns down temperature to `away` setting. Uses the default vacation switch if left blank.

---

### 🪟 Window Sensors

The app will set the climate temperature to the `away` setting for as long as windows are open. It will also notify if the indoor temperature drops below the `normal` threshold. You can also specify a temperature threshold with `getting_cold` to only get notifications if a window is open and the temperature outside is below getting cold. This defaults to 18 degrees.

Define a window temperature sensor as `window_temp` to react quicker to sunny days and turn down heating before it gets too hot, with `window_offset` as an offset from target temperature. This is default to -3.

---

### 🌞 Daylight Savings

The `daylight_savings` has a start and stop time. The time accepts the start time before midnight and the stop time after midnight. In addition, you can define presence so that it does not apply daylight savings.

---

### 📩 Recipients

Define custom recipients per climate or use recipients defined in the main configuration.

---

### 📄 Example Configuration

Define climate entity with `heater`.

```yaml
  climate:
    - heater: climate.floor_thermostat
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

---

## 🔌 Switches

Hot-water boilers with no temperature sensors and only an on/off switch can also be controlled using the app's functionality. It will utilize ElectricityPrice functions to find optimal times for heating or turning on the heater. If a power consumption sensor is provided, it will enable more accurate calculations to avoid exceeding the maximum usage limit in ElectricalUsage.

Define switch entity with `switch`.

```yaml
  heater_switches:
    - switch: switch.hotwater
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

---

## 📄 Contributions

Pull request against the dev branch is much appriciated.

---

## 📄 Example App Configuration

Putting it all together in a configuration. All numbers are default values

```yaml
electricity:
  module: electricalManagement
  class: ElectricalUsage
  dependencies:
    - electricalPriceCalc
  electricalPriceApp: electricalPriceCalc

  # Control maximum electricity usage
  power_consumption: sensor.power_home
  accumulated_consumption_current_hour: sensor.accumulated_consumption_current_hour_home
  max_kwh_goal: 15
  buffer: 0.4

  # Set home in vacation mode and turn down temperatures
  vacation: input_boolean.vacation

  # Notification options
  notify_receiver:
    - mobile_app_yourphone
    - mobile_app_yourotherphone
  infotext: input_text.information
  home_name: home

  # Configuration options
  options:
    - pause_charging
    - notify_overconsumption

  # Cars and Chargers
  startBeforePrice: 0.01
  stopAtPriceIncrease: 0.3

  tesla:
    - charger: yourTesla
      pref_charge_limit: 90
      finishByHour: input_number.finishChargingAt
      priority: 3
      charge_now: input_boolean.charge_Now

  easee:
    - charger: nameOfCharger
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
    - heater: climate.floor_thermostat
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
        - out: -4
          offset: 2
        - out: -3
          offset: 1
        - out: 2
          offset: 0
        - out: 7
          offset: -2
        - out: 11
          offset: -3
        - out: 14
          offset: -5
        - out: 17
          offset: -6

  heater_switches:
    - switch: switch.hotwater
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

---

## 📄 Still got questions?

Let me know so I can update this readme

---