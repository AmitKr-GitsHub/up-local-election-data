# UP SEC WebForms Scraper (Option 5 & 6)

This repository includes a resumable scraper for:

- `5` → Gram Panchayat Pradhan
- `6` → Gram Panchayat Sadashya

Target page:
`https://sec.up.nic.in/site/DownloadCandidateFaDebt.aspx`

## Features

- Handles ASP.NET WebForms postback flow (`__VIEWSTATE`, `__EVENTVALIDATION`, etc.)
- Walks cascade dropdowns: Post Type → District → Block → GP → Ward
- Captures tables and no-data messages
- Resume-safe with done-keys and JSON progress files
- Outputs separate Excel files for option `5` and `6`

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install requests beautifulsoup4 lxml pandas openpyxl
python scrape_up_sec.py --out-dir outputs --post-types 5 6 --checkpoint-every 50
```

Expected outputs:

- `outputs/pradhan_option_5.xlsx`
- `outputs/sadashya_option_6.xlsx`

## Resume after interruption

Re-run the same command; scraper skips already completed combinations using:

- `outputs/post_5_done_keys.txt`
- `outputs/post_6_done_keys.txt`

Progress snapshots are saved in:

- `outputs/post_5_progress.json`
- `outputs/post_6_progress.json`

## Google Colab

Open `up_sec_scraper_colab.ipynb`, then:

1. Install dependencies.
2. Upload `scrape_up_sec.py`.
3. Run scrape cell.
4. Download generated Excel files.
