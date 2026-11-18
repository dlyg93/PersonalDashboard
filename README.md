# Persoonlijk dashboard

Een dashboard voor mijn forex-accounts (MT5), mijn Telenet TV-opnameruimte en Strava.

```
Mac mini (Windows)                      Google Drive                 GitHub Pages
collector/collector.py  --elke 5 min-->  dashboard.json  <--leest--  index.html (na Google-login)
  MT5, Telenet MCP, Strava API           (privé)                     (publiek, maar leeg zonder login)
```

Op deze site staat **geen data**. De pagina toont pas iets nadat je inlogt met het Google-account
dat eigenaar is van `dashboard.json`. Zo lang de Google-app in de testmodus staat, kunnen alleen de
testgebruikers die je zelf toevoegt inloggen.

## 1. Google-login instellen (eenmalig, ~15 min)

1. Ga naar <https://console.cloud.google.com/> en maak een project, bv. `persoonlijk-dashboard`.
2. **APIs & Services → Library**: zoek **Google Drive API** en klik **Enable**.
3. **APIs & Services → OAuth consent screen** (of "Google Auth Platform"):
   - User type **External**, app-naam `Persoonlijk dashboard`, je eigen e-mailadres.
   - Scope toevoegen: `.../auth/drive.readonly`.
   - Bij **Test users / Audience**: voeg je eigen Gmail-adres toe.
   - Laat de app in **Testing** staan (niet publiceren).
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Type **Web application**.
   - **Authorized JavaScript origins**: `https://<jouw-github-naam>.github.io`
     (alleen het domein, zonder pad of `/` op het einde).
   - Kopieer de **Client ID**.
5. Plak die Client ID in `config.js` bij `googleClientId` en commit.

De eerste keer toont Google "Deze app is niet geverifieerd". Dat is normaal voor een eigen app
in testmodus: klik op **Doorgaan**.

## 2. Strava koppelen op de Mac mini (eenmalig, ~5 min)

1. Ga naar <https://www.strava.com/settings/api> en maak een app:
   - Website: je GitHub Pages-adres.
   - **Authorization Callback Domain**: `localhost`.
2. Dubbelklik `strava_koppelen.bat` in de Drive-map, plak de Client ID en Client Secret,
   en klik in de browser op **Toestaan**.
3. De sleutels komen in `%USERPROFILE%\.personal-dashboard\strava.json`, dus **niet** in Drive of GitHub.

## 3. De collector op de Mac mini

Zie de map `collector/`. Kort: `installeer.bat` → paden van je MT5-terminals invullen in
`config.json` → `installeer.bat` opnieuw. Daarna draait het elke 5 minuten via Taakplanner.
`test.bat` toont per bron wat er lukt of misgaat.

## Problemen

| Wat je ziet | Oorzaak |
|---|---|
| "Nog geen Google Client ID ingevuld" | `config.js` nog aanpassen (stap 1.5) |
| Google: `origin_mismatch` | het adres bij Authorized JavaScript origins klopt niet |
| Google: `access_denied` | je e-mailadres staat niet bij de testgebruikers |
| "Mac mini heeft al … niets gestuurd" | Mac mini staat uit/slaapt, of de geplande taak loopt niet |
| Na een uur "sessie verlopen" | Google-tokens gelden een uur; klik op Doorgaan |
