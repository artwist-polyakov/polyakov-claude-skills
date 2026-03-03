# API Reference: Dimensions & Metrics

## Endpoints

| Endpoint | Description |
|----------|-------------|
| `/stat/v1/data` | Table report |
| `/stat/v1/data/bytime` | Time-series report (use with `group`) |
| `/stat/v1/data/drilldown` | Hierarchical drill-down |
| `/stat/v1/data/comparison` | Segment comparison |

Append `.csv` for CSV format: `/stat/v1/data.csv`

## Visit Metrics (prefix: ym:s:)

| Metric | Description |
|--------|-------------|
| `ym:s:visits` | Total visits |
| `ym:s:users` | Unique visitors |
| `ym:s:pageviews` | Page views |
| `ym:s:bounceRate` | Bounce rate |
| `ym:s:pageDepth` | Pages per visit |
| `ym:s:avgVisitDurationSeconds` | Avg visit duration (sec) |
| `ym:s:crossDeviceUsers` | Cross-device unique visitors |

## Goal Metrics (replace `<goal_id>`)

| Metric | Description |
|--------|-------------|
| `ym:s:goal<goal_id>visits` | Visits with goal achieved |
| `ym:s:goal<goal_id>reaches` | Total goal achievements |
| `ym:s:goal<goal_id>conversionRate` | Conversion rate (visits) |
| `ym:s:goal<goal_id>userConversionRate` | Conversion rate (users) |
| `ym:s:goal<goal_id>users` | Users who achieved goal |

## Traffic Source Dimensions (replace `<attribution>`)

Attribution values: `lastsign`, `last`, `first`

| Dimension | Description |
|-----------|-------------|
| `ym:s:<attr>TrafficSource` | Traffic source type |
| `ym:s:<attr>SourceEngine` | Detailed source (search engine name, etc.) |
| `ym:s:<attr>AdvEngine` | Ad system |
| `ym:s:<attr>ReferalSource` | Referral website |
| `ym:s:<attr>RecommendationSystem` | Recommendation system |
| `ym:s:<attr>Messenger` | Messenger |

## UTM Dimensions (replace `<attribution>`)

| Dimension | Description |
|-----------|-------------|
| `ym:s:<attr>UTMSource` | utm_source |
| `ym:s:<attr>UTMMedium` | utm_medium |
| `ym:s:<attr>UTMCampaign` | utm_campaign |
| `ym:s:<attr>UTMContent` | utm_content |
| `ym:s:<attr>UTMTerm` | utm_term |

## Device & Technology Dimensions

| Dimension | Description |
|-----------|-------------|
| `ym:s:deviceCategory` | desktop / mobile / tablet |
| `ym:s:operatingSystem` | OS |
| `ym:s:browser` | Browser |
| `ym:s:screenResolution` | Screen resolution |

## Geography Dimensions

| Dimension | Description |
|-----------|-------------|
| `ym:s:regionCountry` | Country |
| `ym:s:regionCity` | City |
| `ym:s:regionArea` | Region/area |

## Common Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `ids` | Counter ID(s), comma-separated | required |
| `date1` | Start date (YYYY-MM-DD) | required |
| `date2` | End date (YYYY-MM-DD) | today |
| `metrics` | Metrics, comma-separated | required |
| `dimensions` | Dimensions, comma-separated | - |
| `filters` | Filter expression | - |
| `accuracy` | 0-1, where 1 = no sampling | 0.5 |
| `group` | day / week / month (bytime only) | - |
| `limit` | Max rows | 100 |
| `offset` | Row offset for pagination | 1 |
| `sort` | Sort field (prefix `-` for desc) | - |

## Filter Syntax

```
ym:s:isRobot=='No'
ym:s:deviceCategory=='desktop'
ym:s:lastSignTrafficSource=='organic'
ym:s:regionCountry=='Россия' AND ym:s:deviceCategory=='mobile'
```

Operators: `==`, `!=`, `=@` (contains), `!@` (not contains), `=~` (regex), `!~` (not regex)
Combine with `AND`, `OR`.
