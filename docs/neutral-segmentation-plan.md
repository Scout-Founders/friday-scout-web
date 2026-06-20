# Neutral Segmentation Plan (Option C)

**Status:** Implemented in Research Memory analytics and gate intelligence refresh  
**Version:** `option_c_v1`  
**Constraint:** No scoring, gate, Stable Signal, or explainability logic changes

---

## Rationale

Horizon-1 Scout Memory accumulated a large **Neutral** book (~45% of completed outcomes in early 2026 audits) that:

- Uses **bullish-style outcome labels** (WIN when forward return > 0) while often being **bear-leaning** on internal scores
- **Inverts** pooled Threat Scan and gate-intelligence metrics when blended with Bullish/Bearish
- **Inflates or deflates** blended win rate without representing actionable directional edge

**Option C** keeps all historical rows intact but **segments analytics**:

| Book | Directions | Role |
|------|------------|------|
| **Actionable** | Bullish, Bearish | Directional KPIs, gate intelligence refresh |
| **Research (Neutral)** | Neutral | Audit, regime probes, watchlist, learning — not actionable WR |

No migration deletes data. `final_direction` on each row is unchanged.

---

## Three Outcome Classes

| Class | `final_direction` | In actionable win rate? | In gate intelligence refresh? |
|-------|-------------------|-------------------------|-------------------------------|
| **Bullish** | `Bullish` | Yes | Yes |
| **Bearish** | `Bearish` | Yes | Yes |
| **Neutral** | `Neutral` | No | No |

All three remain:

- Stored in `scan_results`
- Visible in Research Memory history (filters, CSV export)
- Included in **mixed** win rate (backward-compatible `win_rate` field)

---

## Formulas

### Completed outcome set

A row is **completed** when:

```text
stock_outcome_label IN ('WIN', 'LOSS', 'FLAT')
AND COALESCE(is_test_record, 0) = 0
```

`FLAT` is included in **counts** but excluded from win-rate denominators (same as prior behavior).

### Mixed win rate (all directions)

```text
mixed_win_rate = wins / (wins + losses) × 100
```

Where `wins` / `losses` are summed over **all** completed rows (Bullish + Bearish + Neutral).

**API fields:** `win_rate`, `mixed_win_rate` (identical values for backward compatibility).

### Actionable win rate

```text
actionable_win_rate = actionable_wins / (actionable_wins + actionable_losses) × 100
```

Where wins/losses are summed only for:

```text
final_direction IN ('Bullish', 'Bearish')
```

### Direction counts (completed)

```text
actionable_bullish_count = COUNT(completed WHERE final_direction = 'Bullish')
actionable_bearish_count  = COUNT(completed WHERE final_direction = 'Bearish')
actionable_total          = actionable_bullish_count + actionable_bearish_count
```

### Neutral universe

```text
neutral_count       = COUNT(completed WHERE final_direction = 'Neutral')
neutral_percentage  = neutral_count / total_completed × 100
neutral_avg_score   = AVG(scout_score) on completed Neutral rows
neutral_win_rate    = wins / (wins + losses) on Neutral only (research / audit)
```

### Segmentation impact

```text
win_rate_delta_actionable_minus_mixed = actionable_win_rate − mixed_win_rate
```

Positive delta → Neutral book was dragging blended win rate down.  
Negative delta → Neutral book was inflating blended win rate.

---

## KPI Definitions

### Actionable universe (primary)

| KPI | Definition | Use |
|-----|------------|-----|
| **Actionable win rate** | Bullish + Bearish label WR | Primary directional performance |
| **Bullish count** | Completed Bullish rows | Book size |
| **Bearish count** | Completed Bearish rows | Book size |
| **Actionable total** | Bullish + Bearish completed | Denominator context |
| **Bullish / Bearish win rate** | Per-direction label WR | Direction splits |

### Neutral universe (research)

| KPI | Definition | Use |
|-----|------------|-----|
| **Neutral count** | Completed Neutral rows | Research book size |
| **Neutral %** | Share of all completed | Concentration risk |
| **Neutral avg score** | Mean `scout_score` | Confidence calibration context |
| **Neutral win rate** | Neutral-only label WR | Audit only — not actionable |

### Comparison (dashboard)

| KPI | Definition |
|-----|------------|
| **Mixed win rate** | All directions — legacy headline |
| **Actionable win rate** | Bullish + Bearish only |
| **Delta (pp)** | Actionable − Mixed |

---

## Implementation Surface

| Layer | Behavior |
|-------|----------|
| `memory_store.get_outcome_analytics()` | Emits segmented fields + `segmentation` metadata |
| `memory_store.refresh_gate_intelligence_metrics()` | **Excludes** Neutral rows |
| Research Memory UI | Actionable / Neutral sections + comparison card |
| `scan_results` / gates / scores | **Unchanged** |

---

## Future Reporting Recommendations

1. **Default dashboards** to **Actionable win rate**; show Mixed only in comparison card.
2. **Gate intelligence** and predictive rankings: always filter `final_direction IN ('Bullish','Bearish')`.
3. **Cohort tags** (`cohort_class`, `scanPurpose`): keep ETF / failure-learning / Neutral research quarantined from actionable denominators.
4. **Weekly review:** track `neutral_percentage`; target ↓ over time via universe rotation, not by deleting Neutral scans.
5. **Do not** map bear-leaning Neutral → Bearish (Option B) without an explicit outcome-label policy change.
6. **Export / BI:** add `is_actionable` column derived from `final_direction` for external tools.

---

## Backward Compatibility

| Item | Status |
|------|--------|
| Historical rows | Preserved |
| `win_rate` field | Still mixed (all directions) |
| History table / filters | Neutral still selectable |
| Scoring / gates / SS / explainability | Frozen |
| Test records | Excluded from analytics (`is_test_record = 0`) |

---

## Related Documents

- [Structured Universe Expansion Plan](./structured-universe-expansion-plan.md)
- Statistical Diversification Phase 1 (universe presets + cohort metadata)
- Neutral Reclassification Design Review (session artifact — Option C recommended)
