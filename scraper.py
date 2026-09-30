import os
import json
import requests
from guessit import guessit
from pathlib import Path

CONFIG_FILE = "vault_config.json"
CATALOG_FILE = "catalog.json"

def load_config():
    default_config = {
        "tmdb_api_key": "",
        "movie_dir": "./hot_tier",
        "poster_dir": "./posters",
        "husk_daemon_url": "http://127.0.0.1:8080"
    }
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return {**default_config, **json.load(f)}
    return default_config

def save_config(config_data):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config_data, f, indent=4)

def load_existing_catalog():
    """Loads existing catalog and creates an index by original_file."""
    if not os.path.exists(CATALOG_FILE):
        return {}, []
    try:
        with open(CATALOG_FILE, 'r') as f:
            catalog = json.load(f)
            catalog_map = {item['original_file']: item for item in catalog}
            return catalog_map, catalog
    except Exception as e:
        print(f"[!] Warning reading catalog: {e}")
        return {}, []

def run_scraper(progress_callback=None):
    config = load_config()
    api_key = config.get("tmdb_api_key", "").strip()
    movie_dir = config.get("movie_dir", "./hot_tier")
    poster_dir = config.get("poster_dir", "./posters")

    os.makedirs(movie_dir, exist_ok=True)
    os.makedirs(poster_dir, exist_ok=True)

    catalog_map, original_catalog = load_existing_catalog()
    valid_exts = ('.mp4', '.mkv', '.avi', '.iso', '.m2ts')

    # Detect current files in hot tier
    current_files = [f for f in os.listdir(movie_dir) if f.endswith(valid_exts)]
    current_files_set = set(current_files)
    total_files = len(current_files)
    processed_count = 0

    # Prune files that were deleted from the folder
    pruned = [fname for fname in list(catalog_map.keys()) if fname not in current_files_set]
    if pruned:
        for fname in pruned:
            print(f"[-] Removed deleted file from catalog: {fname}")
            del catalog_map[fname]

    print(f"[*] Starting incremental scan. {total_files} candidate file(s) found in {movie_dir}.")

    for filename in current_files:
        processed_count += 1
        filepath = os.path.join(movie_dir, filename)
        file_mtime = os.path.getmtime(filepath)

        # ----------------------------------------------------------------------
        # INCREMENTAL CHECK:
        # If entry exists and hasn't changed, retain metadata and skip TMDb API.
        # ----------------------------------------------------------------------
        if filename in catalog_map:
            cached_entry = catalog_map[filename]
            if cached_entry.get("_mtime") == file_mtime and os.path.exists(cached_entry.get("poster", "")):
                # Ensure Resident status since it's present on the hot filesystem
                cached_entry["tier"] = "HOT"
                cached_entry["status"] = "RESIDENT"
                if progress_callback:
                    progress_callback(processed_count, total_files, f"Skipped (cached): {filename}")
                continue

        # Parse filename
        parsed = guessit(filename)
        title = parsed.get('title')
        year = parsed.get('year', '')

        if not title:
            print(f"[?] Could not deduce title for {filename}. Skipping.")
            continue

        if progress_callback:
            progress_callback(processed_count, total_files, f"Scraping TMDb: {title} ({year})")

        print(f"[{processed_count}/{total_files}] Searching TMDb: {title} ({year})")

        tmdb_data = {}
        local_poster_path = "posters/placeholder.jpg"

        if api_key:
            try:
                search_url = f"https://api.themoviedb.org/3/search/movie"
                params = {"api_key": api_key, "query": title, "year": year}
                res = requests.get(search_url, params=params, timeout=10)
                data = res.json()

                if data.get('results'):
                    tmdb_data = data['results'][0]
                    poster_path = tmdb_data.get('poster_path')

                    if poster_path:
                        local_poster_path = f"{poster_dir}/{tmdb_data['id']}.jpg"
                        if not os.path.exists(local_poster_path):
                            img_url = f"https://image.tmdb.org/t/p/w500{poster_path}"
                            img_res = requests.get(img_url, timeout=15)
                            if img_res.status_code == 200:
                                with open(local_poster_path, 'wb') as handler:
                                    handler.write(img_res.content)
            except Exception as e:
                print(f"[!] TMDb lookup failed for {title}: {e}")

        # Retain cold storage info if this file already had physical placement recorded
        existing_meta = catalog_map.get(filename, {})

        catalog_map[filename] = {
            "id": tmdb_data.get("id", existing_meta.get("id", filename)),
            "original_file": filename,
            "title": tmdb_data.get("title", title),
            "year": year or tmdb_data.get("release_date", "")[:4],
            "overview": tmdb_data.get("overview", existing_meta.get("overview", "")),
            "poster": local_poster_path if os.path.exists(local_poster_path) else existing_meta.get("poster", "posters/placeholder.jpg"),
            # Tier states: HOT (Resident), NEARLINE (Sleeping Drive), COLD (Shelf)
            "tier": "HOT",
            "status": "RESIDENT",
            "location_label": existing_meta.get("location_label", "Hot Tier (SSD)"),
            "shelf_info": existing_meta.get("shelf_info", None), 
            "_mtime": file_mtime
        }

    # Write the reconciled catalog back to disk
    updated_catalog = list(catalog_map.values())
    with open(CATALOG_FILE, 'w') as f:
        json.dump(updated_catalog, f, indent=4)

    print("[✓] Incremental scan completed successfully.")
    return updated_catalog

def search_tmdb(query):
    """Searches TMDb for candidate matches."""
    config = load_config()
    api_key = config.get("tmdb_api_key", "").strip()
    if not api_key or not query:
        return []

    try:
        url = "https://api.themoviedb.org/3/search/movie"
        res = requests.get(url, params={"api_key": api_key, "query": query}, timeout=8)
        data = res.json()
        results = []
        for item in data.get("results", [])[:8]:
            poster = f"https://image.tmdb.org/t/p/w200{item.get('poster_path')}" if item.get('poster_path') else "posters/placeholder.jpg"
            results.append({
                "id": item.get("id"),
                "title": item.get("title"),
                "year": (item.get("release_date") or "")[:4],
                "overview": item.get("overview", ""),
                "poster_preview": poster
            })
        return results
    except Exception as e:
        print(f"[!] TMDb search query failed: {e}")
        return []

def update_movie_match(original_file, tmdb_id):
    """Re-matches a movie by TMDb ID, downloads poster, and updates catalog.json."""
    config = load_config()
    api_key = config.get("tmdb_api_key", "").strip()
    poster_dir = config.get("poster_dir", "./posters")
    os.makedirs(poster_dir, exist_ok=True)

    catalog_map, _ = load_existing_catalog()
    if original_file not in catalog_map:
        return {"error": "File not found in catalog"}

    existing_entry = catalog_map[original_file]
    tmdb_data = {}
    local_poster_path = existing_entry.get("poster", "posters/placeholder.jpg")

    if api_key:
        try:
            url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
            res = requests.get(url, params={"api_key": api_key}, timeout=10)
            if res.status_code == 200:
                tmdb_data = res.json()
                poster_path = tmdb_data.get("poster_path")
                if poster_path:
                    local_poster_path = f"{poster_dir}/{tmdb_data['id']}.jpg"
                    img_res = requests.get(f"https://image.tmdb.org/t/p/w500{poster_path}", timeout=15)
                    if img_res.status_code == 200:
                        with open(local_poster_path, "wb") as f:
                            f.write(img_res.content)
        except Exception as e:
            return {"error": f"Failed fetching TMDb ID {tmdb_id}: {e}"}

    # Reconcile metadata while preserving archive tier & shelf info
    existing_entry.update({
        "id": tmdb_data.get("id", tmdb_id),
        "title": tmdb_data.get("title", existing_entry.get("title")),
        "year": (tmdb_data.get("release_date") or "")[:4] or existing_entry.get("year"),
        "overview": tmdb_data.get("overview", existing_entry.get("overview")),
        "poster": local_poster_path
    })

    catalog_map[original_file] = existing_entry
    with open(CATALOG_FILE, 'w') as f:
        json.dump(list(catalog_map.values()), f, indent=4)

    return {"status": "success", "entry": existing_entry}

if __name__ == "__main__":
    run_scraper()
