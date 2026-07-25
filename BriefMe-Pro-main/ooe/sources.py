from __future__ import annotations

import json
import re
import ssl
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache
from html import unescape
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import certifi

from .config import resolve_resource_path


TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
LOOKUP_TEXT_RE = re.compile(r"[^a-z0-9]+")
COUNTRY_INDEX_URL = "https://restcountries.com/v3.1/all?fields=name,capital,latlng,capitalInfo,altSpellings"
COUNTRY_CACHE_SECONDS = 30 * 24 * 3600
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

# Short aliases that are genuinely ambiguous — e.g. "Georgia" is a US state
# and a country; "Jordan" is a common personal name and a country.
# We require that at least one of these context words appears in the same text
# before we accept a match on an ambiguous alias.
_AMBIGUOUS_ALIASES: dict[str, frozenset[str]] = {
    # country alias → context words that confirm it's the country
    "georgia":    frozenset({"tbilisi", "caucasus", "georgian", "south ossetia", "abkhazia"}),
    "jordan":     frozenset({"amman", "jordanian", "petra", "hashemite", "dead sea"}),
    "guinea":     frozenset({"conakry", "guinean", "west africa", "sahel"}),
    "niger":      frozenset({"niamey", "nigerien", "sahel", "west africa"}),
    "chad":       frozenset({"ndjamena", "chadian", "lake chad", "sahel"}),
    "mali":       frozenset({"bamako", "malian", "sahel", "timbuktu"}),
    "iran":       frozenset({"tehran", "iranian", "persian", "ayatollah", "khamenei", "rouhani"}),
    "virginia":   frozenset({"richmond", "roanoke"}),  # US state not a country, kept for safety
    "georgia us": frozenset({"atlanta", "savannah"}),  # guard against US state resolution
}

# US state names that might be confused with country names — exclude them from
# country resolution unless a strong country-context word is also present.
_US_STATE_NAMES: frozenset[str] = frozenset({
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee",
    "texas", "utah", "vermont", "virginia", "washington", "west virginia",
    "wisconsin", "wyoming",
})

DEMONYM_ALIASES = {
    "american": "United States",
    "british": "United Kingdom",
    "danish": "Denmark",
    "french": "France",
    "german": "Germany",
    "greek": "Greece",
    "indian": "India",
    "iranian": "Iran",
    "israeli": "Israel",
    "italian": "Italy",
    "japanese": "Japan",
    "kenyan": "Kenya",
    "mexican": "Mexico",
    "nigerian": "Nigeria",
    "pakistani": "Pakistan",
    "philippine": "Philippines",
    "philippines": "Philippines",
    "russian": "Russia",
    "spanish": "Spain",
    "swedish": "Sweden",
    "taiwanese": "Taiwan",
    "thai": "Thailand",
    "turkish": "Turkey",
    "ukrainian": "Ukraine",
}


def clean_text(value: Any) -> str:
    text = unescape(str(value or ""))
    text = TAG_RE.sub(" ", text)
    return WHITESPACE_RE.sub(" ", text).strip()


def normalize_lookup_text(value: Any) -> str:
    return LOOKUP_TEXT_RE.sub(" ", clean_text(value).lower()).strip()


def first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return ""


def first_non_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def descendant_texts(node: ET.Element, names: Iterable[str]) -> list[str]:
    wanted = {name.lower() for name in names}
    values: list[str] = []
    for child in node.iter():
        if local_name(child.tag).lower() in wanted:
            if child.text and child.text.strip():
                values.append(clean_text(child.text))
    return values


def first_descendant_text(node: ET.Element, *names: str) -> str:
    values = descendant_texts(node, names)
    return values[0] if values else ""


def first_link_value(node: ET.Element) -> str:
    for child in node.iter():
        if local_name(child.tag).lower() != "link":
            continue
        href = child.attrib.get("href")
        if href:
            return href.strip()
        if child.text and child.text.strip():
            return child.text.strip()
    return ""


def extract_coordinates(node: ET.Element) -> tuple[float | None, float | None]:
    lat_text = first_descendant_text(node, "lat")
    lon_text = first_descendant_text(node, "long", "lon")

    if lat_text and lon_text:
        try:
            return float(lat_text), float(lon_text)
        except ValueError:
            return None, None

    point_text = first_descendant_text(node, "point")
    if point_text:
        parts = point_text.replace(",", " ").split()
        if len(parts) >= 2:
            try:
                return float(parts[0]), float(parts[1])
            except ValueError:
                return None, None

    return None, None


@dataclass
class BaseSource:
    name: str
    config: dict[str, Any]
    settings: dict[str, Any]

    def collect(self) -> list[dict[str, Any]]:
        raise NotImplementedError


class HTTPSource(BaseSource):
    def fetch_json(self, url: str) -> Any:
        return json.loads(self.fetch_text(url))

    def fetch_text(self, url: str) -> str:
        timeout = float(self.settings.get("collector", {}).get("request_timeout_seconds", 20))
        request = Request(url, headers={"User-Agent": "OOE/1.0"})
        with urlopen(request, timeout=timeout, context=SSL_CONTEXT) as response:
            return response.read().decode("utf-8", errors="replace")


class CountryResolver:
    def __init__(self, runtime_dir: str | Path) -> None:
        self.cache_path = Path(runtime_dir).expanduser() / "country_index_cache.json"
        self._aliases = self._load_aliases()

    def _load_aliases(self) -> list[dict[str, Any]]:
        payload = self._load_payload()
        records = self._build_records(payload)
        aliases: list[dict[str, Any]] = []

        by_country = {record["country"]: record for record in records}
        for record in records:
            for alias, is_capital in record["aliases"]:
                normalized = normalize_lookup_text(alias)
                # Require at least 4 characters to avoid matching noise like "US", "UK"
                # (2-letter ISO codes are handled via explicit DEMONYM_ALIASES above)
                if len(normalized) < 4:
                    continue
                aliases.append(
                    {
                        "alias": normalized,
                        "country": record["country"],
                        "region": record["country"],
                        "location_name": record["capital"] if is_capital and record["capital"] else record["country"],
                        "latitude": record["latitude"],
                        "longitude": record["longitude"],
                    }
                )

        for alias, country in DEMONYM_ALIASES.items():
            record = by_country.get(country)
            if not record:
                continue
            aliases.append(
                {
                    "alias": normalize_lookup_text(alias),
                    "country": record["country"],
                    "region": record["country"],
                    "location_name": record["capital"] if record["capital"] else record["country"],
                    "latitude": record["latitude"],
                    "longitude": record["longitude"],
                }
            )

        aliases.sort(key=lambda item: (-len(item["alias"].split()), -len(item["alias"])))
        return aliases

    def _load_payload(self) -> list[dict[str, Any]]:
        if self.cache_path.exists() and (time.time() - self.cache_path.stat().st_mtime) < COUNTRY_CACHE_SECONDS:
            try:
                return json.loads(self.cache_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        try:
            request = Request(COUNTRY_INDEX_URL, headers={"User-Agent": "OOE/1.0"})
            with urlopen(request, timeout=20, context=SSL_CONTEXT) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(payload), encoding="utf-8")
            return payload
        except Exception:
            if self.cache_path.exists():
                return json.loads(self.cache_path.read_text(encoding="utf-8"))
            raise

    def _build_records(self, payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for entry in payload:
            name = clean_text((entry.get("name") or {}).get("common") or "")
            official = clean_text((entry.get("name") or {}).get("official") or "")
            capital = clean_text((entry.get("capital") or [""])[0] if isinstance(entry.get("capital"), list) else "")
            capital_latlng = (entry.get("capitalInfo") or {}).get("latlng") or []
            country_latlng = entry.get("latlng") or []
            latlng = capital_latlng if len(capital_latlng) >= 2 else country_latlng
            latitude = float(latlng[0]) if len(latlng) >= 2 else None
            longitude = float(latlng[1]) if len(latlng) >= 2 else None
            aliases: set[tuple[str, bool]] = set()
            for alias in [name, official, *(entry.get("altSpellings") or [])]:
                alias_text = clean_text(alias)
                if alias_text:
                    aliases.add((alias_text, False))
            if capital:
                aliases.add((capital, True))
            if not name or latitude is None or longitude is None:
                continue
            records.append(
                {
                    "country": name,
                    "capital": capital,
                    "latitude": latitude,
                    "longitude": longitude,
                    "aliases": sorted(aliases),
                }
            )
        return records

    def resolve(self, text: str) -> dict[str, Any]:
        haystack = f" {normalize_lookup_text(text)} "
        if not haystack.strip():
            return {}
        for entry in self._aliases:
            alias = entry["alias"]
            if f" {alias} " not in haystack:
                continue

            # ── Disambiguation guard ───────────────────────────────────────
            # If the alias is a known US state name, skip unless a strong
            # non-US-state country context word is present.
            if alias in _US_STATE_NAMES:
                # Check whether any known country context word appears
                context_words = _AMBIGUOUS_ALIASES.get(alias, frozenset())
                if context_words and not any(f" {cw} " in haystack for cw in context_words):
                    continue   # looks like a US state reference — skip

            # If the alias is in our ambiguous list, require at least one
            # disambiguation context word.
            if alias in _AMBIGUOUS_ALIASES:
                context_words = _AMBIGUOUS_ALIASES[alias]
                if not any(f" {cw} " in haystack or cw in haystack for cw in context_words):
                    continue   # ambiguous without corroborating context — skip

            return {
                "country": entry["country"],
                "region": entry["region"],
                "location_name": entry["location_name"],
                "latitude": entry["latitude"],
                "longitude": entry["longitude"],
            }
        return {}


@lru_cache(maxsize=8)
def get_country_resolver(runtime_dir: str) -> CountryResolver:
    return CountryResolver(runtime_dir)


class LocalFileSource(BaseSource):
    def collect(self) -> list[dict[str, Any]]:
        config_path = Path(str(self.settings["config_path"]))
        path = resolve_resource_path(str(self.config["path"]), config_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            items = payload.get("events", [])
            return [dict(item) for item in items if isinstance(item, dict)]
        if isinstance(payload, list):
            return [dict(item) for item in payload if isinstance(item, dict)]
        return []


class DirectoryInboxSource(BaseSource):
    def collect(self) -> list[dict[str, Any]]:
        config_path = Path(str(self.settings["config_path"]))
        path = resolve_resource_path(str(self.config["path"]), config_path)
        path.mkdir(parents=True, exist_ok=True)

        events: list[dict[str, Any]] = []
        for file_path in sorted(path.glob("*.json")):
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, dict) and isinstance(payload.get("events"), list):
                events.extend(dict(item) for item in payload["events"] if isinstance(item, dict))
            elif isinstance(payload, dict):
                events.append(dict(payload))
        return events


class USGSSource(HTTPSource):
    def collect(self) -> list[dict[str, Any]]:
        payload = self.fetch_json(str(self.config["url"]))
        features = payload.get("features", []) if isinstance(payload, dict) else []
        results: list[dict[str, Any]] = []

        for feature in features:
            properties = feature.get("properties", {}) if isinstance(feature, dict) else {}
            geometry = feature.get("geometry", {}) if isinstance(feature, dict) else {}
            coordinates = geometry.get("coordinates", []) if isinstance(geometry, dict) else []
            longitude = coordinates[0] if len(coordinates) > 0 else None
            latitude = coordinates[1] if len(coordinates) > 1 else None
            magnitude = properties.get("mag") or 0
            place = properties.get("place") or "Seismic zone"
            title = properties.get("title") or f"Earthquake detected near {place}"
            status = properties.get("status") or "unknown"
            tsunami = properties.get("tsunami")

            tags = ["earthquake", "usgs", "seismic"]
            if tsunami:
                tags.append("tsunami")

            results.append(
                {
                    "id": feature.get("id") or properties.get("code") or title,
                    "external_id": feature.get("id") or properties.get("code") or "",
                    "title": clean_text(title),
                    "summary": clean_text(
                        f"Magnitude {magnitude} event reported near {place}. USGS status: {status}."
                    ),
                    "category": "disaster",
                    "magnitude": magnitude,
                    "location_name": clean_text(place),
                    "region": clean_text(place),
                    "latitude": latitude,
                    "longitude": longitude,
                    "happened_at": properties.get("time"),
                    "url": properties.get("url") or "",
                    "tags": tags,
                    "raw_payload": feature,
                }
            )

        return results


class EONETSource(HTTPSource):
    def collect(self) -> list[dict[str, Any]]:
        payload = self.fetch_json(str(self.config["url"]))
        entries = payload.get("events", []) if isinstance(payload, dict) else []
        results: list[dict[str, Any]] = []

        for entry in entries:
            categories = entry.get("categories", []) if isinstance(entry, dict) else []
            geometry = entry.get("geometry", []) if isinstance(entry, dict) else []
            latest_geometry = geometry[-1] if geometry else {}
            coordinates = latest_geometry.get("coordinates", []) if isinstance(latest_geometry, dict) else []

            longitude = coordinates[0] if len(coordinates) > 0 else None
            latitude = coordinates[1] if len(coordinates) > 1 else None
            category_title = ""
            if categories and isinstance(categories[0], dict):
                category_title = clean_text(categories[0].get("title") or "")

            tags = [clean_text(category.get("title") or "") for category in categories if isinstance(category, dict)]
            tags = [tag for tag in tags if tag]

            title = clean_text(entry.get("title") or "Open environmental event")
            description = category_title or "Open Earth observation event"
            results.append(
                {
                    "id": entry.get("id") or title,
                    "external_id": entry.get("id") or "",
                    "title": title,
                    "summary": clean_text(f"{description}. Event remains open in NASA EONET."),
                    "category": category_title.lower() if category_title else "environment",
                    "location_name": title,
                    "region": title,
                    "latitude": latitude,
                    "longitude": longitude,
                    "happened_at": latest_geometry.get("date") or entry.get("closed") or entry.get("geometry", [{}])[0].get("date"),
                    "url": entry.get("link") or "",
                    "tags": tags or ["environment", "eonet"],
                    "raw_payload": entry,
                }
            )

        return results


class RSSSource(HTTPSource):
    def collect(self) -> list[dict[str, Any]]:
        xml_text = self.fetch_text(str(self.config["url"]))
        root = ET.fromstring(xml_text)
        items = [node for node in root.iter() if local_name(node.tag).lower() in {"item", "entry"}]
        feed_happened_at = first_descendant_text(root, "lastBuildDate", "updated", "pubDate", "modified", "date")
        limit = int(self.config.get("limit") or 80)
        results: list[dict[str, Any]] = []
        try:
            resolver = get_country_resolver(str(self.settings.get("runtime_dir") or ""))
        except Exception:
            resolver = None

        fallback_tags = [clean_text(self.config.get("category") or "")]
        fallback_tags = [tag for tag in fallback_tags if tag]
        source_tier = str(self.config.get("source_tier") or "trusted").strip().lower()
        allowed_domains = [
            str(domain).strip().lower()
            for domain in (self.config.get("allowed_domains") or [])
            if str(domain).strip()
        ]

        for item in items[:limit]:
            title = first_descendant_text(item, "title") or "Untitled feed event"
            summary = (
                first_descendant_text(item, "description", "summary", "content", "encoded")
                or title
            )
            happened_at = (
                first_descendant_text(
                    item,
                    "pubDate",
                    "updated",
                    "published",
                    "date",
                    "modified",
                    "issued",
                    "dc:date",
                    "dcterms:issued",
                )
                or feed_happened_at
            )
            link = first_link_value(item)
            if allowed_domains and link:
                parsed = urlparse(link)
                hostname = (parsed.netloc or "").lower()
                if not any(hostname == domain or hostname.endswith(f".{domain}") for domain in allowed_domains):
                    continue
            guid = first_descendant_text(item, "guid", "id") or link or title
            category_values = descendant_texts(item, {"category", "subject", "tag"})
            latitude, longitude = extract_coordinates(item)
            inferred_location = resolver.resolve(f"{title} {summary}") if resolver else {}
            fallback_country = clean_text(self.config.get("fallback_country") or "")
            country = clean_text(first_non_empty(fallback_country, inferred_location.get("country"), ""))
            region = clean_text(first_non_empty(self.config.get("fallback_region"), inferred_location.get("region"), country))
            location_name = clean_text(
                first_non_empty(
                    self.config.get("fallback_location_name"),
                    inferred_location.get("location_name"),
                    region,
                    country,
                    "Global",
                )
            )

            results.append(
                {
                    "id": guid,
                    "external_id": guid,
                    "title": clean_text(title),
                    "summary": clean_text(summary),
                    "category": clean_text(self.config.get("category") or (category_values[0] if category_values else "")),
                    "location_name": location_name,
                    "country": country,
                    "region": region,
                    "latitude": first_non_none(latitude, self.config.get("fallback_latitude"), inferred_location.get("latitude")),
                    "longitude": first_non_none(longitude, self.config.get("fallback_longitude"), inferred_location.get("longitude")),
                    "happened_at": happened_at,
                    "url": link,
                    "source_tier": source_tier,
                    "tags": [*fallback_tags, *category_values],
                    "raw_payload": {
                        "title": clean_text(title),
                        "summary": clean_text(summary),
                        "categories": category_values,
                        "link": link,
                        "published": happened_at,
                        "source_tier": source_tier,
                    },
                }
            )

        return results


def build_sources(settings: dict[str, Any]) -> list[BaseSource]:
    sources: list[BaseSource] = []
    for source_config in settings.get("sources", []):
        if not source_config.get("enabled", True):
            continue

        kind = str(source_config.get("kind", "")).strip().lower()
        name = str(source_config.get("name") or kind or "Unnamed source")

        if kind == "local_file":
            sources.append(LocalFileSource(name=name, config=source_config, settings=settings))
        elif kind == "directory_inbox":
            sources.append(DirectoryInboxSource(name=name, config=source_config, settings=settings))
        elif kind == "usgs_geojson":
            sources.append(USGSSource(name=name, config=source_config, settings=settings))
        elif kind == "eonet_open":
            sources.append(EONETSource(name=name, config=source_config, settings=settings))
        elif kind == "rss":
            sources.append(RSSSource(name=name, config=source_config, settings=settings))

    return sources
