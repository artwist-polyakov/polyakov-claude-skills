#!/usr/bin/env python3
"""Convert a public Reddit Atom feed to the cached Reddit Listing format."""

import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit


ATOM = "{http://www.w3.org/2005/Atom}"
REDDIT = "https://www.reddit.com"


class PostHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.link = ""
        self.anchor_href = ""
        self.anchor_text = []

    def handle_starttag(self, tag, attrs):
        if tag in {"p", "div", "br", "li"}:
            self.text.append("\n")
        if tag == "a":
            self.anchor_href = dict(attrs).get("href") or ""
            self.anchor_text = []

    def handle_endtag(self, tag):
        if tag in {"p", "div", "li"}:
            self.text.append("\n")
        if tag == "a":
            if "".join(self.anchor_text).strip() == "[link]":
                self.link = self.anchor_href
            self.anchor_href = ""
            self.anchor_text = []

    def handle_data(self, data):
        self.text.append(data)
        if self.anchor_href:
            self.anchor_text.append(data)

    def plain_text(self):
        lines = (" ".join(line.split()) for line in "".join(self.text).splitlines())
        return "\n".join(line for line in lines if line)


def atom_text(element, name):
    return (element.findtext(ATOM + name) or "").strip()


def parse_entry(entry):
    name = atom_text(entry, "id")
    if not re.fullmatch(r"t3_[a-z0-9]+", name):
        raise ValueError("entry has no valid Reddit post id")
    post_id = name[3:]
    title = atom_text(entry, "title")
    if not title:
        raise ValueError(f"{name}: missing title")

    permalink = ""
    for link in entry.findall(ATOM + "link"):
        if link.get("rel", "alternate") != "alternate":
            continue
        url = urlsplit(urljoin(REDDIT, link.get("href", "")))
        if (url.scheme in {"http", "https"}
                and url.hostname in {"reddit.com", "www.reddit.com", "old.reddit.com"}
                and f"/comments/{post_id}/" in url.path + "/"):
            permalink = url.path
            break
    if not permalink:
        raise ValueError(f"{name}: missing Reddit post link")

    published = atom_text(entry, "published") or atom_text(entry, "updated")
    date = datetime.fromisoformat(published.replace("Z", "+00:00"))
    if date.tzinfo is None:
        raise ValueError(f"{name}: publication date has no timezone")

    author = atom_text(entry, "author/" + ATOM + "name").lstrip("/")
    if author.startswith("u/"):
        author = author[2:]
    category = entry.find(ATOM + "category")
    subreddit = ""
    if category is not None:
        subreddit = category.get("term") or category.get("label", "")
        if subreddit.startswith("r/"):
            subreddit = subreddit[2:]

    content = atom_text(entry, "content")
    html = PostHTML()
    html.feed(content)
    html.close()
    post_url = REDDIT + permalink
    linked_url = urljoin(post_url, html.link) if html.link else post_url
    if urlsplit(linked_url).scheme not in {"http", "https"}:
        linked_url = post_url

    return {"kind": "t3", "data": {
        "id": post_id, "name": name, "title": title,
        "permalink": permalink, "subreddit": subreddit, "author": author,
        "created_utc": date.timestamp(), "selftext": html.plain_text(),
        "selftext_html": content or None, "url": linked_url,
        "score": None, "num_comments": None,
    }}


def convert(input_path, output_path):
    feed = ET.parse(input_path).getroot()
    if feed.tag != ATOM + "feed":
        raise ValueError("expected an Atom feed")
    if any(child.tag.rsplit("}", 1)[-1] == "entry" and child.tag != ATOM + "entry"
           for child in feed):
        raise ValueError("entry has no Atom namespace")
    children = [parse_entry(entry) for entry in feed.findall(ATOM + "entry")]
    result = {"kind": "Listing", "source": "rss", "data": {
        "children": children, "after": None, "before": None,
    }}
    Path(output_path).write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("Usage: rss_to_listing.py INPUT_ATOM OUTPUT_JSON")
    try:
        convert(sys.argv[1], sys.argv[2])
    except (OSError, ValueError, ET.ParseError) as error:
        sys.exit(f"[reddit-skill] Invalid RSS feed: {error}")
