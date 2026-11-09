# i-DE (Iberdrola Distribución) Custom Integration for Home Assistant

<!-- HomeAssistant badges -->
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![hassfest validation](https://github.com/ldotlopez/ha-ideenergy/workflows/Validate%20with%20hassfest/badge.svg)](https://github.com/ldotlopez/ha-ideenergy/actions/workflows/hassfest.yml)
[![HACS validation](https://github.com/ldotlopez/ha-ideenergy/workflows/Validate%20with%20HACS/badge.svg)](https://github.com/ldotlopez/ha-ideenergy/actions/workflows/hacs.yml)

<!-- Code and releases -->
![GitHub Release (latest SemVer including pre-releases)](https://img.shields.io/github/v/release/ldotlopez/ha-ideenergy?include_prereleases)
[![CodeQL](https://github.com/ldotlopez/ha-ideenergy/actions/workflows/codeql-analysis.yml/badge.svg)](https://github.com/ldotlopez/ha-ideenergy/actions/workflows/codeql-analysis.yml)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/ambv/black)

[ideenergy](https://github.com/ldotlopez/ideenergy) integration for [home-assistant](https://home-assistant.io/)

i-DE (Iberdrola Distribución) Custom Integration for Home Assistant, providing sensors for Spanish Energy Distributor [i-DE](https://i-de.es).

This integration requires an **advanced** user profile on i-DE website.

**⚠️ Make sure to read the '[FAQ](https://github.com/ldotlopez/ha-ideenergy/blob/main/FAQ.md)', 'Dependencies' and 'Warning' sections**


> [!NOTE]
> **This is the `jmarpas` fork** of [ldotlopez/ha-ideenergy](https://github.com/ldotlopez/ha-ideenergy)
> (GPL-2.0-or-later, original author Luis López). It adds stability fixes for the
> mass HTTP 403 errors caused by ideenergy 2.0.1.dev3 never re-logging in: 13-minute
> session timeout, re-login + single retry on 401/403, per-dataset exponential backoff
> (5 min → 30 min → 2 h → 6 h), reauth flow and masked email in logs.
> See [docs/fork-phase1-diagnosis.md](docs/fork-phase1-diagnosis.md).
>
> **Also in the fork (v3.0.0a3-jaime.2+):** *Instant Power* sensor (W, refreshed with each
> meter reading), *Historical Consumption P1/P2/P3* statistics (2.0TD breakdown from i-DE),
> 0 kWh hours kept, correct hours across DST changes (25 h in October / 23 h in March),
> Spanish translation, diagnostics, and a backfill service:
>
> ```yaml
> action: ideenergy.backfill_historical_consumption
> data:
>   config_entry_id: <your i-DE entry>
>   start: "2025-01-01"   # i-DE keeps about two years
>   end: "2025-12-31"     # optional, defaults to today
> ```
>
> **Install the fork with HACS** (requires Home Assistant 2026.3.0 or newer):
>
> 1. HACS → ⋮ → *Custom repositories* → URL `https://github.com/jmarpas/ha-ideenergy`, type *Integration*.
> 2. If the upstream integration is installed from HACS, remove that HACS repository first
>    (**do not delete the integration entry** in *Settings → Devices & services*). The
>    domain (`ideenergy`) and entity unique_ids are the same, so statistics history is kept.
> 3. Download the latest release (`v3.0.0a3-jaime.*`) and restart HA.
> 4. On start, HA installs `ideenergy` from GitHub (commit `f7b3118`), so internet access to github.com is needed.

## Features

* Integration with the Home Assistant Energy Panel.

* Accumulated and Instant consumption sensors.

* Historical sensors (both consumption and solar generation) with better (sub-kWh) precision. This data is not realtime and usually has a 24-hour to 48-hour offset.

* Support for multiple contracts (service points).

* Configuration through [Home Assistant Interface](https://developers.home-assistant.io/docs/config_entries_options_flow_handler) without the need to edit YAML files.

* Update algorithm to read the meter near the end of each hourly period (between minute 50 and 59)
with a better representation of consumption in the Home Assistant energy panel.

* Fully [asynchronous](https://developers.home-assistant.io/docs/asyncio_index) and integrated with HomeAssistant.


## Dependencies

You must have an i-DE username and access to the Clients' website. You may register here: [Área Clientes | I-DE - Grupo Iberdrola](https://www.i-de.es/consumidores/web/guest/login).

It also necessary to have an "Advanced User" profile. Should you not have one already, you need to fill the request for from your [Profile Area](https://www.i-de.es/consumidores/web/home/personal-area/userData).


## Installation

### Using [HACS](https://hacs.xyz/) (recommended)

1. Copy this repository URL: [https://github.com/ldotlopez/ha-ideenergy](https://github.com/ldotlopez/ha-ideenergy/)

2. In the HACS section, add this repository as a custom one:


  - On the "repositorysitory" field put the URL copied before
  - On the "Category" select "Integration"
  - Click the "Download" button and download latest version.

  ![Custom repositorysitory](https://user-images.githubusercontent.com/59612788/171965822-4a89c14e-9eb2-4134-8de2-1d3f380663e4.png)

3. Restart HA

4. Configure the integration

  - (Option A) Click the "Add integration" button → [![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=ideenergy)

  - (Option B) Go to "Settings"  "Devices & Services" and click "+ ADD INTEGRATION" and select "i-de.es energy sensors".  
    ![image](https://user-images.githubusercontent.com/59612788/171966005-e58f6b88-a952-4033-82c6-b1d4ea665873.png)

5. Follow the configuration steps: provide your credentials for access to i-DE and select the contract that you want to monitor. (Should you need to add more contracts, just follow the previous step as many times as needed).


### Manually

1. Download/clone this repository: [https://github.com/ldotlopez/ha-ideenergy](https://github.com/ldotlopez/ha-ideenergy/)

2. Copy the `custom_components/ideenergy` folder into your custom_components folder into your HA installation

3. Restart HA

4. Configure the integration

  - (Option A) Click on this button → [![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=ideenergy)
  - (Option B) Go to "Settings" → "Devices & Services" and click "+ ADD INTEGRATION" and select "i-de.es energy sensors".  
    ![image](https://user-images.githubusercontent.com/59612788/171966005-e58f6b88-a952-4033-82c6-b1d4ea665873.png)

5. Follow the configuration steps: provide your credentials for access to i-DE and select the contract that you want to monitor. (Should you need to add more contracts, just follow the previous step as many times as needed).

## Snapshots

*Accumulated energy sensor*

![snapshot](screenshots/accumulated.png)

*Historical energy sensor*

![snapshot](screenshots/historical.png)

*Configuration wizard*

![snapshot](screenshots/configuration-1.png)
![snapshot](screenshots/configuration-2.png)

## Warnings
This extension provides an 'historical' sensor to incorporate data from the past into Home Assistant database. For your own safety the sensor is not enabled by default and must be enabled manually.

☠️ Historic sensor is based on a **high experimental hack** and can broke and/or corrupt your database and/or statistics. **Use at your own risk**.

## License

This project is licensed under the GNU General Public License v3.0 License - see the LICENSE file for details


## Disclaimer

THIS PROJECT IS NOT IN ANY WAY ASSOCIATED WITH OR RELATED TO THE IBERDROLA GROUP COMPANIES OR ANY OTHER. The information here and online is for educational and resource purposes only and therefore the developers do not endorse or condone any inappropriate use of it, and take no legal responsibility for the functionality or security of your devices.
