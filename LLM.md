# ad-ElectricalManagement: notes for an AI assistant

Read this with [README.md](README.md). The workflow for finding the user's real entity ids (token, `ha_snapshot.py`, asking when several match) is in the [Pythm_AppdaemonApps LLM guide](https://github.com/Pythm/Pythm_AppdaemonApps/blob/master/LLM_GUIDE.md). Follow its rules: use ids from the index only, ask when several match, one app per `apps.yaml` edit, back up first, no commit or push unasked. The code is the reference; where this file and the README differ, this file follows the code.

## Finding the user's entities (short version; the full workflow is in the guide above)

1. The user creates a long-lived access token in Home Assistant (profile, Security tab) and saves it in a plain-text file `.local/ha_token` in the Pythm_AppdaemonApps folder. The assistant never opens or prints that file and never asks for the token in chat.
2. `python3 tools/ha_snapshot.py --init` creates the file; after the user has filled it in, `python3 tools/ha_snapshot.py --url http://<ha-address>:8123` writes `.local/ha_index.jsonl` (one entity per line: id, name, room, integration, device class, unit, state). It only reads.
3. Search that index for the entity types in the table below. Use only ids found there. When several entities match, ask the user which one (and how it is used) instead of choosing. Without a token, ask the user for the ids.
4. Write one app at a time into `apps.yaml` after backing up the file, and check the app log for WARNING/ERROR lines.

## What the app does

One app per home (`module: electricalManagement`, `class: ElectricalUsage`). It **acts on its own** every minute, so the user must say yes to each part before it is enabled:

- **Cars and chargers:** plans charging in the cheapest hours from ElectricalPriceCalc and starts, stops and sets the amps of Tesla (Tesla custom integration), Easee, Audi (untested on a real car) and generic cars.
- **Hourly kWh cap:** keeps the hour under `max_kwh_goal`. It lowers charger amps first, then reduces heaters and switches, and with `options: [pause_charging]` it stops charging.
- **Heaters and switches:** save periods before a price drop and spend periods before a rise. `climate_commander:` entries get a power state that the ClimateCommander app turns into a setpoint. Deprecated `climate:` entries get a setpoint from this app. `heater_switches:` are switched on and off.
- **Fire alarm:** `MODE_CHANGE` with mode `fire` stops charging and turns off heaters and switches.
- **Learning:** idle consumption, the top 3 hours, battery size and heater consumption after save periods are stored in a JSON file (`json_path`, default `<config_dir>/persistent/electricity/electricalmanagement.json`; it is a file path, not a folder).

Needs [ElectricalPriceCalc](https://github.com/Pythm/ElectricalPriceCalc) (required) and [ad-Weather](https://github.com/Pythm/ad-Weather) (`WEATHER_CHANGE` gives the outdoor temperature; without it the last stored value is used, 10 at first start).

## Config (apps.yaml)

```yaml
electricity:
  module: electricalManagement
  class: ElectricalUsage
  dependencies:                   # only names of apps that exist in apps.yaml
    - electricalPriceCalc
    - notify_mobiles
  electricalPriceApp: electricalPriceCalc     # required, the exact app name
  power_consumption: sensor.house_power
  accumulated_consumption_current_hour: sensor.house_energy_this_hour
  max_kwh_goal: 10
  vacation: input_boolean.vacation
  notify_app: notify_mobiles
  notify_receiver: [Me]
  climate_commander:
    - app: heater_bathroom        # name of a ClimateCommander app
      power: 1500                 # rated watts, from the sensor history or the user
      kWhconsumptionSensor: sensor.bathroom_floor_energy
      max_continuous_hours: 4
  heater_switches:
    - switch: switch.water_heater
      power: 2000
      consumptionSensor: sensor.water_heater_power
      max_continuous_hours: 8
      on_for_minimum: 8
```

The top-level keys are validated by `ElectricalManagementConfig` in `pydantic_models.py`. A wrong type gives `Configuration error in '<key>'` at ERROR and the app does not start. Unknown top-level keys are ignored. The list entries are read as plain dicts, so a misspelled key in an entry is silently ignored.

| Key | Type | Default | Notes |
|---|---|---|---|
| `electricalPriceApp` | str | required | Name of the ElectricalPriceCalc app. |
| `main_namespace` | str | `default` | HA namespace for sensors, events and notifications. Each list entry may set its own `namespace`. |
| `notify_app` | str | none | App with `send_notification(...)`, normally `notify_mobiles`. Without it: HA `notify/<receiver>` services. |
| `notify_receiver` | str or list | `[]` | Person labels (with `notify_app`) or notify service names (without). |
| `home_name` | str | `home` | Label in notifications only. The car location is always compared with the zone `home`. |
| `json_path` | str | see above | Full path of the JSON file. |
| `power_consumption` | entity | none | House power in W. Without it AND the next key: no kWh cap, the queue is checked every 10 min. |
| `accumulated_consumption_current_hour` | entity | none | kWh this hour, resets to 0 every hour. |
| `power_production`, `accumulated_production_current_hour` | entity | none | Solar (untested with real data). |
| `max_kwh_goal` | float | 15 | Hourly cap in kWh. The cap in use is stored in the JSON and rises by 5 when the average of the top 3 hours exceeds it. |
| `buffer` | float | 0.4 | kWh kept free under the cap. |
| `options` | list | `[]` | `notify_overconsumption`, `notify_overconsumption_also_when_away`, `pause_charging`. Others: WARNING. |
| `automate` | bool or entity | `true` | Default `automate` for every heater entry. |
| `vacation` | entity | `input_boolean.vacation` if it exists | Main vacation switch. `away_state` is an older name; if both are set, `away_state` wins. |
| `infotext` | entity | none | `input_text` that shows the planned charge time. |
| `startBeforePrice`, `stopAtPriceIncrease` | float | 0.01, 0.3 | Charging window margins. |
| `tesla`, `audi`, `cars`, `easee`, `climate`, `climate_commander`, `heater_switches` | list of dicts | `[]` | See below. |

### Car entries (`tesla`, `audi`, `cars`)

- `tesla`: `car` (alias `charger`) is the name in the Tesla entity ids. Without it the name is taken from `charger_sensor`. Entities are found as `<domain>.<car><suffix>`, and a key in the entry overrides that: `charger_sensor` (binary_sensor `_charger`), `charge_limit` (number `_charge_limit`), `battery_sensor` (`_battery`), `asleep_sensor`, `online_sensor` (its `id` attribute is the vehicle id), `location_tracker`, `destination_location_tracker`, `arrival_time`, `software_update`, `force_data_update`, `polling_switch`, `data_last_update_time`. The charger part: `charger_switch` (switch `_charger`), `charging_amps` (number `_charging_amps`), `charger_power`, `session_energy` (`_energy_added`). Also `wake_when_stale` (true) and `unlink_grace_minutes` (12, 0 = unlink at once).
- `audi` (untested, say so): `car` or `plug_state` (binary_sensor `_plug_state`), `charging_state` (sensor), `charging_power` (kW, alias of `charger_power`), `charge_limit` (number `_global_charge_target`), `battery_sensor` (`_state_of_charge`), `location_tracker` (`_position`, its `vin` attribute is the id), `data_last_update_time`, `device_id` (HA device id; without it a WARNING and the VIN is used), `verify_minutes` (20), `max_command_retries` (2), `charge_power_kW`.
- `cars` (generic): `carName` is required (WARNING and skipped without it), the same sensor keys as Tesla (found as `<domain>.<carName><suffix>`), plus `maxChargerAmpere` (32), `volts` (220), `phases` (1), `min_ampere` (6), `idle_current`, `guest`. Without `location_tracker` the car always counts as home.
- Every car: `battery_size` (100, learned; a configured value replaces the learned one at every start), `pref_charge_limit` (90), `charge_below_price` (0 = off, else charge whenever the price is below it), `priority` (3; 1 and 2 finish charging after the price rises), `finishByHour` (7, an integer or an `input_number`), `charge_now` and `charge_only_on_solar` (false or an `input_boolean`), `namespace`. `departure` is read but not implemented: do not add it.

### `easee`

`charger` (the name in `sensor.<charger>_status`; without it, derived from `charger_status`). `status` or `charger_status` is the status sensor (= `charger_sensor`), `current` = `charging_amps`. Found automatically if not given: `_status`, `_reason_for_no_current`, `_current`, `_power`, `_energy_added`, `_voltage`, `_max_charger_limit`, `_idle_current` (keys `reason_for_no_current`, `charger_power`, `session_energy`, `voltage`, `max_charger_limit`, `idle_current`). Optional `guest` (`input_boolean`), `guest_remove_after_minutes` (12), `namespace`. `guest` is read only the first time the charger is saved in the JSON (see the owner question in the report).

### Heater entries (`climate_commander`, `heater_switches`, deprecated `climate`)

Identity: `climate_commander` needs `app` (the CC app name; `climate`, alias `heater`, only for an old CC app with `HVAC:`/`Heaters:` lists). `heater_switches` needs `switch`. `climate` needs `heater` (an entity id; `name` no longer works). Energy keys, shared by all three:

| Key | Default | Notes |
|---|---|---|
| `power` | stored value, else 300 (climate, CC) / 1000 (switch) | Rated watts. ALWAYS set; find from the device's power sensor history, else ask. |
| `consumptionSensor` | `sensor.<name>_electric_consumption_w` or `_electric_consumed_w` if it exists | W sensor of this device only. |
| `kWhconsumptionSensor` | `..._electric_consumption_kwh` or `_electric_consumed_kwh` | Needed to learn the consumption after a save period. |
| `max_continuous_hours`, `on_for_minimum` | 2, 6 | Save period length and check against low prices. |
| `pricedrop`, `pricedifference_increase` | 1, 1.07 | When a save period is found. |
| `priceincrease`, `low_price_max_continuous_hours` | 1, ElectricalPriceCalc default (3) | Spend periods; `climate` and `climate_commander` only. |
| `automate` | top-level `automate` | `false` or an `input_boolean` that is off: no save periods. Spend and the kWh cap still apply. |
| `vacation` | main vacation switch (CC: the CC app's vacation switch) | An entity id string, or leave it out. |
| `recipient` | `notify_receiver` | Used by `climate` window alerts and switch "finished" alerts. |
| `options` | `[]` | `print_save_hours`; `vacation_keep_off` (only `climate` and `heater_switches`; for CC set it in the CC app). |
| `namespace` | `main_namespace` | |

`heater_switches` only: `notify_when_finished` (false), `turn_off_after` / `turn_off_before` (`'22:00:00'` / `'07:00:00'`, quote them), `start_threshold` / `stop_threshold` (100 / 15 W), `start_duration` / `stop_duration` (30 / 30 s), `turn_back_on_after` (60 s: a power cycle; 0: stays off until someone turns it on). On vacation a switch is on mainly in the cheapest 2 h of the day (off all the time with `vacation_keep_off`).

`climate` only (deprecated, do not write in new configs): `indoor_sensor_temp`, `target_indoor_input`, `target_indoor_temp` (23), `target_heater_input`, `target_heater_temp` (23), `window_temp`, `window_offset` (-3), `save_temp_offset`, `save_temp`, `vacation_temp`, `rain_level` (3), `anemometer_speed` (40), `getting_cold` (18), `windowsensors`, `daytime_savings`, `temperatures`. ClimateCommander accepts the same keys.

Saved values: for heater and car entries, a key in the YAML replaces the saved value at the next start, but removing a key keeps the saved value (also `vacation`, and `vacation_keep_off` once set through `options`). To turn something off, set it explicitly (for example `notify_when_finished: false`). Top-level defaults (`automate`, `vacation`, `notify_receiver` as `recipient`) are copied into a heater entry only when it is first saved. A later change at the top level does not reach it unless the entry sets the key itself.

## Which entities fit each key

| Key | Entity | Hints when searching the index |
|---|---|---|
| `power_consumption` | `sensor`, device_class `power`, unit `W` | Whole house (HAN reader, Tibber Pulse). Not a single device. |
| `accumulated_consumption_current_hour` | `sensor`, device_class `energy`, unit `kWh` | Must reset every hour. A daily or total meter is wrong. |
| `power_production`, `accumulated_production_current_hour` | as above | Solar inverter or a meter with export. |
| `vacation`, `automate`, `charge_now`, `charge_only_on_solar`, `guest` | `input_boolean` | Helpers: if missing, tell the user what to create. |
| `finishByHour` | `input_number` (or an integer) | |
| `infotext` | `input_text` | |
| `tesla` `car` | the slug in `binary_sensor.<car>_charger` | `"integration":"tesla_custom"`. Two Teslas: one entry each. |
| `easee` `charger` | the slug in `sensor.<charger>_status` | `"integration":"easee"`. Enable the disabled Easee sensors in HA first. |
| `audi` keys | `"integration":"audiconnect"` | `device_id` is the HA device id, not an entity. |
| `climate_commander` `app` | an app name in `apps.yaml` | Not an entity. Check the CC app exists. |
| `heater_switches` `switch` | `switch.*` | Water heater relay, smart plug of a dryer, etc. |
| `consumptionSensor` | `sensor`, device_class `power`, unit `W` | Same device as the heater or switch. |
| `kWhconsumptionSensor` | `sensor`, device_class `energy`, `total_increasing` | Same device. |
| `indoor_sensor_temp`, `window_temp` | `sensor`, device_class `temperature` | In that room. |
| `windowsensors` | `binary_sensor`, device_class `window`/`door`/`opening` | `climate:` only. |
| `daytime_savings` `presence` | `person.*` | |

## ALWAYS ask the user

- **Rated watts for `power:`** on every `climate`, `heater_switches` and `climate_commander` entry. Find them yourself first: if the device has its own power sensor (the `consumptionSensor`, a W sensor), read its history read-only with `tools/ha_peak_power.py` from the [Pythm_AppdaemonApps](https://github.com/Pythm/Pythm_AppdaemonApps) repository (the same clone that holds `tools/ha_snapshot.py` and the `.local/ha_token` file from the [LLM guide](https://github.com/Pythm/Pythm_AppdaemonApps/blob/master/LLM_GUIDE.md); if the user has not cloned it yet, `git clone https://github.com/Pythm/Pythm_AppdaemonApps.git`, or download the single file from its `tools` folder): `python3 tools/ha_peak_power.py <sensor> ...` (it uses the user's HA token from `.local/ha_token`, which only the script reads, and prints max, p99 and the on-plateau of the last 30 days). Use the plateau when it is clear (several samples, max close to the plateau) and write the highest value rounded to 10 W. Tell the user which value you took and from which sensor, so it can be corrected. Only ask the user (nameplate, manual, installer) when there is no sensor, no history, the device never ran in the period, or the plateau is not clear. Never use the old stored `normal_power` from the JSON, EM's learned consumption, or a guess: the app no longer learns it, the old learning only went up, and until 1.0.6 it stored the sum of several heaters. Without `power:` the stored value (or 300 / 1000 W) is used and one WARNING lists the entries.
- **Two sensors of one kind** (two power meters, two temperature sensors in a room, two hourly kWh sensors): is the second a backup, a sensor for another side of the house, or something else? Never decide from names or areas.
- **Who gets notifications, and how.** (1) With `notify_app: notify_mobiles` (check the real app name), `notify_receiver` and `recipient` are the **person labels** of that app. (2) Without `notify_app`, they are HA notify service names such as `mobile_app_phone`. Never mix the two kinds. Ask whether overconsumption alerts are wanted (`notify_overconsumption`), and also when away.
- **Which car can use which charger.** Never assume a car only uses its own charger: either Tesla may charge on either Easee or wall connector. The app links cars and chargers from their states; ask so you can explain what will happen.
- **Before anything that acts alone:** charging control per car, heater and switch control, `automate`, `pause_charging`, `vacation_keep_off`, and the fire alarm turn-off (any app that fires `MODE_CHANGE` `fire` makes this app switch off heaters and stop cars).
- **Vacation:** which `input_boolean`, whether a heater or switch may be off on vacation (`vacation_keep_off`; for CC devices in the CC app).
- **The price app name** in the user's `apps.yaml` for `electricalPriceApp` and `dependencies`.
- **The hourly cap:** `max_kwh_goal` from the user's grid tariff step, never the default 15 unasked.

## Check an existing config (old or faulty keys)

Read the user's block before changing anything, and report:
- `climate:` entries: deprecated (WARNING), they still work. Suggest moving each to a ClimateCommander app listed under `climate_commander:`, one at a time and only with a yes. `climate:` manages only hvac mode `heat`; a climate used for `cool`, `fan_only`, `dry`, `auto` or `heat_cool` must be a CC app.
- Water heaters, dryers and other on/off switches stay in `heater_switches`. Never move them to ClimateCommander.
- A heater entry without `power:` (find the watts from the power sensor history, else ask).
- `windowsensors` on a `heater_switches` entry: ignored, WARNING. Remove it (window handling belongs to `climate:` or the CC app).
- `automate` that is not `true`/`false` or an entity id (WARNING, and automation is on).
- A heater `vacation` that is not an entity id string. A saved `false`/`null` is no longer repaired to the main switch: set `vacation:` on the entry if the heater should follow it.
- `turn_back_on_after`: 60 (default) is a power cycle that silences a beeping appliance, 0 keeps it off until someone turns it on. Ask which the user means.
- `dependencies:` between this app and ClimateCommander apps, either way: remove them (EM finds CC apps by name, and a dependency reloads EM on every CC reload).
- `electricalPriceApp` that does not name an existing app, or a `dependencies` name that is not an app.
- `vacation_keep_off` for a CC device belongs in the CC app (`vacation_keep_off: true`), not in this app.
- `away_state` at the top level (older name for `vacation`), `notify_reciever` (misspelled, ignored), `away` / `away_temp` (ignored since 1.0.2), `charge_on_solar` (the key is `charge_only_on_solar`), `daylight_savings` (the key is `daytime_savings`).
- A new `climate:` entry with `target_indoor_temp` but no `target_heater_temp` gets a heater target of 23: set `target_heater_temp` too.
- Times (`turn_off_after`, `daytime_savings` `start`/`stop`) must be quoted strings `'22:00:00'`.

## Behaviour not to "fix"

- Easee `disconnected` is reported as `awaiting_start` while a car is linked. Cars report wrong data when asleep, and the owner avoids waking them.
- A charge stopped in the Tesla app during planned charge time is started again within a minute.
- The 60 s start loop (also the wake-up for Teslas) has no `unavailable` filter on purpose: the repeated command wakes the car.
- `unlink_grace_minutes` (Tesla default 12) before a Tesla is unlinked from an external charger.
- With two or more cars, a car is linked to the Easee (`awaiting_start`) only on data newer than the cable event: sleeping cars that are not charging are woken (`wake_when_stale: false` stops the WAKE_UP) and force-updated, checked every 30 s for up to 4 min, one round per 10 min. Two cars both reporting `NoPower` on fresh data means no link (Tesla API control through the car's own link still works). Do not link on cached data again: a sleeping Tesla's stale `NoPower` linked the wrong car for 4 hours.
- The 00:03 price refresh does not recalculate heater save/spend periods (they were found at about 13:00).
- `wake_when_stale: false` turns off the WAKE_UP for data older than 12 min; the data update press is still sent.
- Table mode: overconsumption and an open window use the LOWEST of the save and vacation temperature.
- A `temperatures` entry applies from its `out` value and up.
- Fire `false-alarm` turns on only what this app turned off. Heaters and switches stay off until `false-alarm`.
- A window must be open 120 s before the heater is lowered; closing acts at once. A window open at startup acts at once.
- Easee in `ready_to_charge`: 10 resume commands one minute apart, then one every 10 minutes with one WARNING and notification.
- Heaters are never reduced before minute 3 of the hour. Priority 1 and 2 cars keep charging after the price rises.
- The stored cap rises by 5 kWh when the top 3 hours exceed it, and it (with the top hours) goes back to `max_kwh_goal` on the 1st of each month.
- `climate:` setpoints changed by hand are overwritten at the next run.
- Alert and monitoring apps may be meant to fire on `unavailable` / `unknown`: do not filter without asking.

## Events and API for other apps

- Listens to `MODE_CHANGE` (or the translated event name when Lightwand's `translations_lightmodes` is installed) on `main_namespace`: mode `fire` / `false-alarm` (or their translations). Sent by `fire_alarm` and `notify_mobiles` in Pythm_AppdaemonApps, and by ModeManagement.
- Listens to `WEATHER_CHANGE` (`temp`; `climate:` entries also use `rain` and `wind`).
- Listens to `mobile_app_notification_action` for its own notification buttons (find new charge time, add a high consumption hour, guest kWh / charge now, turn off a finished appliance).
- Sends to ClimateCommander devices: `set_power_state(state, valid_minutes = 45, source = 'ElectricalManagement')` with `normal`, `save`, `spend` or `reduce`, re-sent at least every 10 min and within a minute after a CC reload. Without a new call the CC device goes back to `normal` after 45 min. EM also reads `get_device(climate)`, `heater`, `namespace`, `vacation_entity`, `daytime_savings` and `in_daytime_saving()`.
- Fires no events. No app in the sibling repos calls this app with `get_app` (checked 2026-10-23).

## Dependency pitfalls

- `electricalPriceApp` must name an ElectricalPriceCalc app that exists and starts without errors. Otherwise this app logs an ERROR and stops. Put the same name in `dependencies` so the price app is loaded first.
- With `notify_app`, put that app in `dependencies` too: the app is looked up once at startup.
- A `dependencies` name that is not an app in `apps.yaml` stops the dependants from starting. Check every name.
- No `dependencies` from this app to ClimateCommander apps, or back. A `climate_commander` entry whose app is not running yet is retried every 30 s for 10 minutes.
- The Lightwand mode translation needs `dependencies: [mode_translation]` only if that app exists.

## Troubleshooting (log lines)

| Log line | Likely cause |
|---|---|
| `Configuration error in '<key>': ...` (ERROR) | Wrong type for a top-level key. The app does not start. |
| `electricalPriceApp '<name>' was not found ...` (ERROR) | Wrong name, the price app failed, or no `dependencies` on it. |
| `Set 'power' (the heater's rated watts) for: ...` | Entries without `power:`. Find the watts from the sensor history (`tools/ha_peak_power.py`), else ask the user. |
| `'climate:' heaters in ElectricalManagement are deprecated ...` | Old `climate:` list. Works; suggest CC apps. |
| `<climate> is in hvac mode '<mode>' ...` | `climate:` entry not in `heat`. Use a CC app. |
| `<switch>: windowsensors is ignored for switches.` | Remove `windowsensors` from the switch. |
| `<entity>: 'automate' must be a boolean or an entity id ...` | Wrong `automate` value. |
| `ClimateCommander app '<app>' was not found ...` / `... is not running (yet).` | Wrong app name, CC app failed, or an old list app without `climate:` in the entry. |
| `<entity> is configured twice in ElectricalManagement ...` | Same climate in `climate:` and `climate_commander:`. Keep one. |
| `Unknown option '<x>' in options. Ignored.` | Misspelled option. |
| `Electricity prices are not ready yet ...` | Price app has no prices yet. Normal right after a restart. |
| `Avg consumption during one hour is now ... New max kWh ...` | `max_kwh_goal` too low for the home, or `buffer` too small. |
| `<car>: no 'id' attribute on <online_sensor> ...` (ERROR) | Tesla integration not loaded, or a wrong `online_sensor`. Reload after it runs. |
| `Easee <charger> still ready_to_charge after ...` | Car asleep or stopped in the car app. |
| `Could not automatically find <key>: ...` (INFO) | Entity not found by name. Add the key with the id from the index. |
| `Persistence file ... was moved to ... corrupt ...` (ERROR) | Unreadable JSON. The app starts with empty learned data. |

## Rules for the assistant

- One `apps.yaml` edit at a time. Back it up first, show the block, explain in one or two sentences what it will do, and get a yes.
- Entity ids only from the HA index. Never invent ids or app names. Watts come from the sensor history or the user, never from a guess.
- Do not edit the JSON file unless the user asks; stop AppDaemon's app first if they do (it is written on shutdown).
- No commit or push unless asked. No personal entity ids, names or addresses in the docs; use placeholders like `climate.living_room`.
