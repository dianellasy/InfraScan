import { isDateRangeValid } from "./api.ts";
import { EMPTY_FILTERS, ISSUE_TYPES, SEVERITIES, STATUSES } from "./constants.ts";
import { IssueSymbol } from "./IssueSymbol.tsx";
import type { IssueFilters } from "./types.ts";

function toggle<T>(values: T[], value: T): T[] {
  return values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
}

type IssueFiltersPanelProps = {
  value: IssueFilters;
  onChange: (filters: IssueFilters) => void;
};

export function IssueFiltersPanel({ value, onChange }: IssueFiltersPanelProps) {
  const dateRangeValid = isDateRangeValid(value);
  return (
    <form
      className="panel issue-filters"
      aria-label="Issue filters"
      onSubmit={(event) => event.preventDefault()}
    >
      <div className="panel-heading">
        <div>
          <p className="eyebrow">FILTERS</p>
          <h2>Show issues</h2>
        </div>
      </div>

      <fieldset>
        <legend>Issue type</legend>
        {ISSUE_TYPES.map((type) => (
          <label key={type.code} className="check-row">
            <input
              type="checkbox"
              checked={value.issueTypes.includes(type.code)}
              onChange={() => onChange({ ...value, issueTypes: toggle(value.issueTypes, type.code) })}
            />
            <IssueSymbol issueType={type.code} />
            {type.label}
          </label>
        ))}
      </fieldset>

      <fieldset>
        <legend>Severity</legend>
        {SEVERITIES.map((severity) => (
          <label key={severity.value} className="check-row">
            <input
              type="checkbox"
              checked={value.severities.includes(severity.value)}
              onChange={() =>
                onChange({ ...value, severities: toggle(value.severities, severity.value) })
              }
            />
            <span className="severity-dot" style={{ background: severity.color }} aria-hidden="true" />
            {severity.label}
          </label>
        ))}
      </fieldset>

      <fieldset>
        <legend>Status</legend>
        {STATUSES.map((status) => (
          <label key={status.value} className="check-row">
            <input
              type="checkbox"
              checked={value.statuses.includes(status.value)}
              onChange={() => onChange({ ...value, statuses: toggle(value.statuses, status.value) })}
            />
            {status.label}
          </label>
        ))}
      </fieldset>

      <fieldset>
        <legend>Review</legend>
        <label className="check-row">
          <input
            type="checkbox"
            checked={value.reviewRequired}
            onChange={(event) => onChange({ ...value, reviewRequired: event.target.checked })}
          />
          Review required only
        </label>
      </fieldset>

      <fieldset className="date-range">
        <legend>Last seen</legend>
        <label>
          From
          <input
            type="date"
            value={value.lastSeenFrom}
            max={value.lastSeenTo || undefined}
            aria-invalid={!dateRangeValid}
            onChange={(event) => onChange({ ...value, lastSeenFrom: event.target.value })}
          />
        </label>
        <label>
          To
          <input
            type="date"
            value={value.lastSeenTo}
            min={value.lastSeenFrom || undefined}
            aria-invalid={!dateRangeValid}
            onChange={(event) => onChange({ ...value, lastSeenTo: event.target.value })}
          />
        </label>
        {!dateRangeValid && (
          <p className="field-error" role="alert">
            The start date must be on or before the end date. The date range is not applied.
          </p>
        )}
      </fieldset>

      <p className="filter-hint">Leave a group unchecked to include every value.</p>
      <button type="button" className="secondary-button" onClick={() => onChange(EMPTY_FILTERS)}>
        Clear filters
      </button>
    </form>
  );
}
