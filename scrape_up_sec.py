#!/usr/bin/env python3
"""
Scraper for UP SEC ASP.NET WebForms page:
https://sec.up.nic.in/site/DownloadCandidateFaDebt.aspx

Targets post types:
- 5: Gram Panchayat Pradhan
- 6: Gram Panchayat Sadashya

Features:
- Handles WebForms hidden fields (__VIEWSTATE, __EVENTVALIDATION, etc.)
- Cascading dropdown postbacks
- Full traversal of District -> Block -> GP -> Ward (for member)
- Resume support via checkpoint JSONL and state file
- Incremental writes to CSV + final Excel outputs
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

URL = "https://sec.up.nic.in/site/DownloadCandidateFaDebt.aspx"
PANEL_ID = "ctl00_ContentPlaceHolder1_Panel1"

POST_DDL = "ctl00_ContentPlaceHolder1_ddlPostTypes"
DIST_DDL = "ctl00_ContentPlaceHolder1_ddlDistrictName"
BLOCK_DDL = "ctl00_ContentPlaceHolder1_ddlBlockName"
GP_DDL = "ctl00_ContentPlaceHolder1_ddlGpName"
WARD_DDL = "ctl00_ContentPlaceHolder1_ddlGpWardName"

PRADHAN = "5"
SADASHYA = "6"

IGNORE_OPTION_VALUES = {"", "0", "-1"}


@dataclass
class ScrapeConfig:
    out_dir: Path
    checkpoint_every: int = 50
    timeout: int = 45
    min_sleep: float = 0.1
    max_sleep: float = 0.35
    max_retries: int = 5


class WebFormsScraper:
    def __init__(self, config: ScrapeConfig):
        self.cfg = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                ),
                "Referer": URL,
            }
        )

    def _sleep(self) -> None:
        time.sleep(random.uniform(self.cfg.min_sleep, self.cfg.max_sleep))

    def _request(self, method: str, data: Optional[dict] = None) -> str:
        last_err: Optional[Exception] = None
        for i in range(1, self.cfg.max_retries + 1):
            try:
                if method == "GET":
                    r = self.session.get(URL, timeout=self.cfg.timeout)
                else:
                    r = self.session.post(URL, data=data, timeout=self.cfg.timeout)
                r.raise_for_status()
                r.encoding = r.apparent_encoding or "utf-8"
                return r.text
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                if i == self.cfg.max_retries:
                    break
                time.sleep(min(2**i, 12))
        raise RuntimeError(f"Request failed after retries: {last_err}")

    @staticmethod
    def _soup(html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "html.parser")

    @staticmethod
    def hidden_fields(soup: BeautifulSoup) -> Dict[str, str]:
        data: Dict[str, str] = {}
        for inp in soup.select("input[type='hidden'][name]"):
            data[inp.get("name", "")] = inp.get("value", "")
        return data

    @staticmethod
    def options(soup: BeautifulSoup, select_id: str) -> List[Tuple[str, str]]:
        sel = soup.find("select", id=select_id)
        if not sel:
            return []
        out: List[Tuple[str, str]] = []
        for o in sel.find_all("option"):
            v = (o.get("value") or "").strip()
            t = o.get_text(" ", strip=True)
            if v in IGNORE_OPTION_VALUES:
                continue
            out.append((v, t))
        return out

    @staticmethod
    def selected_value(soup: BeautifulSoup, select_id: str) -> str:
        sel = soup.find("select", id=select_id)
        if not sel:
            return ""
        opt = sel.find("option", selected=True)
        return (opt.get("value") if opt else "") or ""

    @staticmethod
    def selected_text(soup: BeautifulSoup, select_id: str) -> str:
        sel = soup.find("select", id=select_id)
        if not sel:
            return ""
        opt = sel.find("option", selected=True)
        return opt.get_text(" ", strip=True) if opt else ""

    def postback(self, soup: BeautifulSoup, target: str, value: str) -> BeautifulSoup:
        payload = self.hidden_fields(soup)
        payload["__EVENTTARGET"] = target
        payload["__EVENTARGUMENT"] = ""
        payload[target] = value

        # Keep selected values for all dropdowns when known.
        for ctl in [POST_DDL, DIST_DDL, BLOCK_DDL, GP_DDL, WARD_DDL]:
            if ctl not in payload:
                payload[ctl] = self.selected_value(soup, ctl)
        payload[target] = value

        html = self._request("POST", payload)
        self._sleep()
        return self._soup(html)

    @staticmethod
    def parse_tables(panel: BeautifulSoup) -> Tuple[List[dict], str]:
        tables = panel.find_all("table") if panel else []
        rows: List[dict] = []
        for ti, tbl in enumerate(tables, start=1):
            headers = [th.get_text(" ", strip=True) for th in tbl.find_all("th")]
            if not headers:
                first_tr = tbl.find("tr")
                headers = [c.get_text(" ", strip=True) for c in first_tr.find_all(["td", "th"])] if first_tr else []
            for tr in tbl.find_all("tr"):
                cells = tr.find_all(["td", "th"])
                if not cells:
                    continue
                vals = [c.get_text(" ", strip=True) for c in cells]
                if headers and vals == headers:
                    continue
                row = {f"col_{i+1}": v for i, v in enumerate(vals)}
                if headers:
                    for i, v in enumerate(vals):
                        if i < len(headers) and headers[i]:
                            row[headers[i]] = v
                row["_table_index"] = ti
                rows.append(row)

        msg_text = ""
        if panel:
            txt = panel.get_text(" ", strip=True)
            if re.search(r"(कोई\s*रिकॉर्ड\s*नहीं|No\s*Record|No\s*Data|रिकॉर्ड\s*उपलब्ध\s*नहीं)", txt, re.I):
                msg_text = txt
        return rows, msg_text

    def initial_page(self) -> BeautifulSoup:
        html = self._request("GET")
        return self._soup(html)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def load_done_keys(path: Path) -> set:
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def append_done_key(path: Path, key: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(key + "\n")


def append_rows_csv(path: Path, rows: List[dict], field_order: Optional[List[str]] = None) -> List[str]:
    if not rows:
        return field_order or []

    keys = set(field_order or [])
    for r in rows:
        keys.update(r.keys())
    ordered = sorted(keys)

    write_header = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ordered, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in ordered})
    return ordered


def scrape_post_type(scraper: WebFormsScraper, post_type: str, post_label: str) -> None:
    out_dir = scraper.cfg.out_dir
    csv_path = out_dir / f"post_{post_type}_raw.csv"
    done_path = out_dir / f"post_{post_type}_done_keys.txt"
    progress_path = out_dir / f"post_{post_type}_progress.json"

    done_keys = load_done_keys(done_path)
    counter = 0
    field_order: Optional[List[str]] = None

    soup = scraper.initial_page()
    soup = scraper.postback(soup, POST_DDL, post_type)

    districts = scraper.options(soup, DIST_DDL)

    state = {
        "post_type": post_type,
        "post_label": post_label,
        "districts_total": len(districts),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    progress_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    for d_val, d_name in districts:
        d_soup = scraper.postback(soup, DIST_DDL, d_val)
        blocks = scraper.options(d_soup, BLOCK_DDL)

        for b_val, b_name in blocks:
            b_soup = scraper.postback(d_soup, BLOCK_DDL, b_val)
            gps = scraper.options(b_soup, GP_DDL)

            for g_val, g_name in gps:
                g_soup = scraper.postback(b_soup, GP_DDL, g_val)

                ward_options = [("", "")]
                if post_type == SADASHYA:
                    w = scraper.options(g_soup, WARD_DDL)
                    ward_options = w if w else [("", "")]

                for w_val, w_name in ward_options:
                    final_soup = g_soup
                    if post_type == SADASHYA and w_val:
                        final_soup = scraper.postback(g_soup, WARD_DDL, w_val)

                    key = "|".join([post_type, d_val, b_val, g_val, w_val])
                    if key in done_keys:
                        continue

                    panel = final_soup.find(id=PANEL_ID)
                    table_rows, message = scraper.parse_tables(panel)

                    if not table_rows:
                        table_rows = [{"status": "no_data", "message": message or "No table rows found."}]

                    enriched: List[dict] = []
                    for r in table_rows:
                        rr = {
                            "post_type": post_type,
                            "post_label": post_label,
                            "district_code": d_val,
                            "district_name": d_name,
                            "block_code": b_val,
                            "block_name": b_name,
                            "gp_code": g_val,
                            "gp_name": g_name,
                            "ward_code": w_val,
                            "ward_name": w_name,
                            "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        }
                        rr.update(r)
                        enriched.append(rr)

                    field_order = append_rows_csv(csv_path, enriched, field_order)
                    append_done_key(done_path, key)
                    done_keys.add(key)
                    counter += 1

                    if counter % scraper.cfg.checkpoint_every == 0:
                        state.update(
                            {
                                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "last_done_key": key,
                                "records_written_batches": counter,
                            }
                        )
                        progress_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    if csv_path.exists():
        df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
        xlsx_path = out_dir / ("pradhan_option_5.xlsx" if post_type == PRADHAN else "sadashya_option_6.xlsx")
        df.to_excel(xlsx_path, index=False)

    state.update({"completed": True, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    progress_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="UP SEC WebForms scraper with resume support.")
    p.add_argument("--out-dir", default="outputs", help="Directory for CSV/Excel/progress files.")
    p.add_argument(
        "--post-types",
        nargs="+",
        default=[PRADHAN, SADASHYA],
        choices=[PRADHAN, SADASHYA],
        help="Post type values to scrape.",
    )
    p.add_argument("--checkpoint-every", type=int, default=50)
    p.add_argument("--timeout", type=int, default=45)
    p.add_argument("--min-sleep", type=float, default=0.1)
    p.add_argument("--max-sleep", type=float, default=0.35)
    p.add_argument("--max-retries", type=int, default=5)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg = ScrapeConfig(
        out_dir=Path(args.out_dir),
        checkpoint_every=args.checkpoint_every,
        timeout=args.timeout,
        min_sleep=args.min_sleep,
        max_sleep=args.max_sleep,
        max_retries=args.max_retries,
    )
    ensure_dir(cfg.out_dir)

    scraper = WebFormsScraper(cfg)

    mapping = {PRADHAN: "Gram Panchayat Pradhan", SADASHYA: "Gram Panchayat Sadashya"}
    for pt in args.post_types:
        scrape_post_type(scraper, pt, mapping[pt])

    print("Completed. Check output directory:", cfg.out_dir)


if __name__ == "__main__":
    main()
