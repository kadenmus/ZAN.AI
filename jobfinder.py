import os
import re
import sqlite3
from datetime import date, datetime

import requests
from dotenv import load_dotenv

# Load API credentials from the .env file
load_dotenv()

APP_ID = os.getenv("ADZUNA_APP_ID")
APP_KEY = os.getenv("ADZUNA_APP_KEY")
BASE_URL = "https://api.adzuna.com/v1/api/jobs/gb/search/1"

# Only show jobs posted on or after this date
EARLIEST_DATE = date(2025, 12, 1)

# Job titles containing any of these words will be filtered out
DEFAULT_EXCLUDE_KEYWORDS = [
    "senior", "manager", "director", "surgeon",
    "solicitor", "accountant", "architect",
    "teacher", "nurse", "therapist", "optometrist",
    "self-employed", "self employed", "registered",
    "ect", "nqt", "chartered", "years experience",
    "supervisor", "coordinator", "gas", "technician",
    "degree apprenticeship", "higher apprenticeship",
    "phd", "intern", "internship", "driver", "driving", "uber"
]

# Default search terms sent to the Adzuna API
DEFAULT_JOB_SEARCHES = [
    "retail",
    "shop assistant",
    "customer service",
    "part time",
    "barista",
    "waiter",
    "kitchen",

]


def prompt(label, default=None):
    """Show an input prompt with an optional default value in brackets.
    Returns the default if the user just presses Enter."""
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{label}{suffix}: ").strip()
    return value if value else default


def get_user_preferences():
    """Interactively ask the user for their search preferences.
    Returns a dict with location, radius, search terms, exclude keywords,
    contract type, and max hours per week."""
    print("=" * 50)
    print("  Job Finder Setup")
    print("=" * 50)

    # --- Location ---
    print("\n-- Location --")
    location = prompt("Town or city", "Welwyn Garden City")
    radius_input = prompt("Search radius (km)", "8")
    try:
        radius = int(radius_input)
    except ValueError:
        # Fall back to default if the user entered something non-numeric
        radius = 8

    # --- Search terms ---
    print("\n-- Job Search Terms --")
    print(f"Defaults: {', '.join(DEFAULT_JOB_SEARCHES)}")

    # Let the user add extra terms on top of the defaults
    extra_terms_input = input("Add extra search terms (comma-separated, or Enter to keep defaults): ").strip()
    search_terms = DEFAULT_JOB_SEARCHES.copy()
    if extra_terms_input:
        search_terms.extend([t.strip() for t in extra_terms_input.split(",") if t.strip()])

    # Let the user remove terms they don't want
    remove_input = input("Remove any default terms? (comma-separated, or Enter to keep all): ").strip()
    if remove_input:
        to_remove = {t.strip().lower() for t in remove_input.split(",")}
        search_terms = [t for t in search_terms if t.lower() not in to_remove]

    # --- Exclude keywords ---
    print("\n-- Exclude Keywords --")
    print(f"Defaults: {', '.join(DEFAULT_EXCLUDE_KEYWORDS)}")

    # User can add extra words to block (e.g. specific job titles they don't want)
    extra_excludes_input = input("Add extra words to exclude (comma-separated, or Enter to keep defaults): ").strip()
    exclude_keywords = DEFAULT_EXCLUDE_KEYWORDS.copy()
    if extra_excludes_input:
        exclude_keywords.extend([e.strip().lower() for e in extra_excludes_input.split(",") if e.strip()])

    # --- Salary cap ---
    print("\n-- Salary Cap --")
    salary_cap_input = input("Max salary cap in £ (e.g. 20000, or Enter for no limit): ").strip()
    try:
        salary_cap = int(salary_cap_input)
    except ValueError:
        salary_cap = None  # No limit if left blank or non-numeric

    # --- Availability / hours per week ---
    print("\n-- Availability --")
    print("  1. Any hours")
    print("  2. Part time only  (< 30 hrs/week)")
    print("  3. Full time only  (30+ hrs/week)")
    print("  4. Custom max hours per week")
    avail_choice = prompt("Choose", "1")

    contract_type = None  # Adzuna API filter: "part_time" or "full_time"
    max_hours = None      # Used to filter listings that mention too many hours

    if avail_choice == "2":
        contract_type = "part_time"
    elif avail_choice == "3":
        contract_type = "full_time"
    elif avail_choice == "4":
        max_input = input("Max hours per week (e.g. 20): ").strip()
        try:
            max_hours = int(max_input)
            # Also tell the API to only return part-time roles
            contract_type = "part_time"
        except ValueError:
            print("Invalid number, ignoring hours filter.")

    # Print a summary of what will be searched
    print("\n" + "=" * 50)
    print(f"  Searching near {location} ({radius}km radius)")
    print(f"  Search terms: {', '.join(search_terms)}")
    if contract_type:
        label = f"part time (max {max_hours} hrs/week)" if max_hours else contract_type.replace("_", " ")
        print(f"  Availability: {label}")
    if salary_cap:
        print(f"  Salary cap:   £{salary_cap:,}")
    print("=" * 50 + "\n")

    return {
        "location": location,
        "radius": radius,
        "search_terms": search_terms,
        "exclude_keywords": exclude_keywords,
        "contract_type": contract_type,
        "max_hours": max_hours,
        "salary_cap": salary_cap,
    }


def init_db():
    """Create the database and seen_jobs table if they don't exist yet.
    Returns a connection to jobs.db in the same folder as the script."""
    conn = sqlite3.connect("jobs.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_jobs (
            id TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            first_seen DATE
        )
    """)
    conn.commit()
    return conn


def filter_seen_jobs(conn, jobs):
    """Remove jobs that are already in the database. Returns only new ones."""
    new_jobs = []
    for job in jobs:
        exists = conn.execute("SELECT 1 FROM seen_jobs WHERE id = ?", (job["id"],)).fetchone()
        if not exists:
            new_jobs.append(job)
    return new_jobs


def save_seen_jobs(conn, jobs):
    """Save newly shown jobs to the database so they're skipped next time."""
    for job in jobs:
        conn.execute(
            "INSERT OR IGNORE INTO seen_jobs (id, title, company, first_seen) VALUES (?, ?, ?, ?)",
            (
                job["id"],
                job["title"],
                job.get("company", {}).get("display_name", "N/A"),
                date.today().isoformat(),
            )
        )
    conn.commit()


def filter_jobs(jobs, exclude_keywords, max_hours=None, salary_cap=None):
    """Remove unwanted jobs from the raw API results.

    Filters out:
    - Duplicate listings (same Adzuna job ID)
    - Titles containing any excluded keyword
    - Jobs posted before EARLIEST_DATE
    - Jobs that mention more hours per week than the user's maximum (if set)
    - Jobs where salary_min already exceeds the user's salary cap (if set)
    """
    filtered = []
    seen_ids = set()  # Track IDs to avoid showing the same job twice

    for job in jobs:
        # Skip duplicates
        if job["id"] in seen_ids:
            continue
        seen_ids.add(job["id"])

        # Skip jobs whose title contains an excluded keyword
        title = job["title"].lower()
        if any(word in title for word in exclude_keywords):
            continue

        # Skip jobs posted before the earliest allowed date
        posted_date = datetime.strptime(job['created'][:10], '%Y-%m-%d').date()
        if posted_date < EARLIEST_DATE:
            continue

        # Skip jobs where the minimum salary already exceeds the cap
        if salary_cap is not None:
            salary_min = job.get("salary_min")
            if salary_min and salary_min > salary_cap:
                continue

        # If the user set a max hours limit, scan the title and description
        # for explicit hour mentions (e.g. "35 hours/week") and skip if over the limit
        if max_hours is not None:
            description = job.get("description", "").lower()
            text = title + " " + description
            hour_matches = re.findall(r'(\d+)\s*(?:hours?(?:\s*/\s*week)?|hrs?(?:\s*/\s*week)?|h/w)', text)
            if hour_matches:
                listed_hours = max(int(h) for h in hour_matches)
                if listed_hours > max_hours:
                    continue

        filtered.append(job)

    return filtered


def fetch_jobs(prefs):
    """Call the Adzuna API for each search term and return filtered results.

    Uses the user's preferences (location, radius, contract type) as API
    parameters, then runs filter_jobs() on the combined results."""
    all_jobs = []

    for search_term in prefs["search_terms"]:
        params = {
            "app_id": APP_ID,
            "app_key": APP_KEY,
            "where": prefs["location"],
            "distance": prefs["radius"],
            "what": search_term,
            "results_per_page": 20,
        }
        # Only add contract_time if the user chose a specific type
        if prefs.get("contract_type"):
            params["contract_time"] = prefs["contract_type"]

        response = requests.get(BASE_URL, params=params)

        if response.status_code == 200:
            results = response.json().get("results", [])
            print(f"  '{search_term}': {len(results)} results")
            all_jobs.extend(results)

    filtered = filter_jobs(all_jobs, prefs["exclude_keywords"], prefs.get("max_hours"), prefs.get("salary_cap"))
    print(f"\nTotal: {len(all_jobs)} fetched, {len(filtered)} after filtering\n")
    return filtered


def display_jobs(jobs):
    """Print each job's details to the terminal in a readable format."""
    if not jobs:
        print("No suitable jobs found.")
        return

    for job in jobs:
        print(f"Title:    {job['title']}")
        print(f"Company:  {job.get('company', {}).get('display_name', 'N/A')}")
        print(f"Location: {job.get('location', {}).get('display_name', 'N/A')}")
        print(f"Salary:   £{job.get('salary_min', 'N/A')} - £{job.get('salary_max', 'N/A')}")
        print(f"Posted:   {job['created'][:10]}")
        # Only show the Hours line if the API returned contract type info
        contract = job.get("contract_time", "")
        if contract:
            print(f"Hours:    {contract.replace('_', ' ')}")
        print(f"Link:     {job['redirect_url']}")
        print("-" * 50)


if __name__ == "__main__":
    prefs = get_user_preferences()
    jobs = fetch_jobs(prefs)

    conn = init_db()
    new_jobs = filter_seen_jobs(conn, jobs)
    print(f"{len(new_jobs)} new jobs (out of {len(jobs)} filtered results)\n")
    display_jobs(new_jobs)
    save_seen_jobs(conn, new_jobs)

    # Show how many jobs are stored in the database in total
    total_seen = conn.execute("SELECT COUNT(*) FROM seen_jobs").fetchone()[0]
    print(f"\n{len(new_jobs)} new jobs saved. {total_seen} total jobs in database.")
    conn.close()
