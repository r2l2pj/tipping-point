# Earthdata token renewal (EARTHDATA_TOKEN)

The current token expires on **2027-01-13 17:21 UTC**, and winners are picked on Jan 26. Renew it before **2027-01-06**. Do not paste the token into chat, a repo, a commit or an issue.

## Where it is used

| Place | Used by | Update needed |
| --- | --- | --- |
| GitHub secret `EARTHDATA_TOKEN` on `r2l2pj/tipping-point-data` | now.yml (every 3 h: IMERG Early, SMAP L4, OPERA DSWx/DIST, NISAR), nisar.yml, validation.yml, reference.yml | **yes**, this one keeps the live site working |
| `~/code/r2l2pj/tipping-point-data/.env` (gitignored, mode 600) | local pipeline/ML runs | yes, if anyone still runs things locally |
| Vercel (tipping-point-app) | not used: the app never calls Earthdata | no |
| `tipping-point-app/.env.local` | not used | no |

The token belongs to the Earthdata account that accepted the GES DISC, ASF and NSIDC EULAs. Generate the new one **on the same account**, otherwise the EULAs must be accepted again on the new one.

## Rotate (3 commands)

1. Generate the token at https://urs.earthdata.nasa.gov/user_tokens (Generate Token) and copy it. Accounts can hold 2 tokens, so the old one keeps working until it expires or is revoked.
2. Run:

```sh
# 1. GitHub secret (paste the token at the hidden prompt)
gh secret set EARTHDATA_TOKEN -R r2l2pj/tipping-point-data
# 2. local .env (paste the token at the hidden prompt; never echoes it)
read -rs T && sed -i "s|^EARTHDATA_TOKEN=.*|EARTHDATA_TOKEN=$T|" ~/code/r2l2pj/tipping-point-data/.env && unset T
# 3. prove it: a now run whose first step rejects a bad token
gh workflow run now.yml -R r2l2pj/tipping-point-data --ref main
```

## Check

- The run's step **"Validate Earthdata token"** is green. It exits with "EARTHDATA_TOKEN expired or rejected" on a bad token, which was tested with a bogus token on 2026-11-15.
- The new expiry date, read without printing the token:

```sh
grep '^EARTHDATA_TOKEN=' ~/code/r2l2pj/tipping-point-data/.env | cut -d= -f2- | python3 -c \
 "import sys,json,base64,datetime as d;p=sys.stdin.read().strip().split('.')[1];p+='='*(-len(p)%4);print(d.datetime.fromtimestamp(json.loads(base64.urlsafe_b64decode(p))['exp'],d.UTC))"
```

- After the GitHub run is green, revoke the old token on the same page.

## If it lapses anyway

now.yml fails at "Validate Earthdata token" and publishes nothing, so the app keeps the last good snapshot. The watchdog then fails with "Now data stale" after 6 h, and GitHub emails the owner. Rotating the token and dispatching now.yml recovers. No data is lost.
