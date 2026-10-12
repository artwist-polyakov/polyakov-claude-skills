#!/bin/sh
# Search for region by name

SEARCH=""

while [ $# -gt 0 ]; do
    case $1 in
        --name|-n) SEARCH="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [ -z "$SEARCH" ]; then
    echo "Usage: search_region.sh --name \"city name\""
    echo ""
    echo "Examples:"
    echo "  bash scripts/search_region.sh --name \"Москва\""
    echo "  bash scripts/search_region.sh --name \"Казань\""
    exit 1
fi

echo "Searching for: $SEARCH"
echo ""

# Hardcoded common regions
REGIONS="
225|Россия
159|Казахстан
187|Украина
149|Беларусь
983|Турция
84|США
3|Центральный ФО
17|Северо-Западный ФО
40|Приволжский ФО
52|Уральский ФО
59|Сибирский ФО
73|Южный ФО
26|Дальневосточный ФО
1|Москва и область
213|Москва
10716|Московская область
2|Санкт-Петербург
10174|Ленинградская область
54|Екатеринбург
65|Новосибирск
43|Казань
35|Краснодар
47|Нижний Новгород
39|Ростов-на-Дону
51|Самара
172|Уфа
56|Челябинск
66|Омск
11|Пермь
14|Воронеж
38|Волгоград
37|Саратов
195|Тюмень
62|Красноярск
68|Томск
67|Барнаул
"

# Search (case-insensitive via python3 for portability). Values go through the
# environment: a quote in the name must not break the code. UTF-8 mode keeps
# Cyrillic names intact under native Windows Python, as in common.sh.
_YSA_SEARCH="$SEARCH" _YSA_REGIONS="$REGIONS" PYTHONUTF8=1 python3 -c '
import os
search = os.environ["_YSA_SEARCH"]
regions = os.environ["_YSA_REGIONS"].strip().split("\n")
found = []
for line in regions:
    line = line.strip()
    if not line:
        continue
    if search.lower() in line.lower():
        parts = line.split("|", 1)
        if len(parts) == 2:
            found.append((parts[0].strip(), parts[1].strip()))

if not found:
    print("No regions found matching \"%s\"" % search)
    print("")
    print("Try running regions_tree.sh to see all common regions")
else:
    print("Found:")
    print("")
    print("| ID | Name |")
    print("|----|------|")
    for rid, name in found:
        print(f"| {rid} | {name} |")
'
