import ModChip from './ModChip.jsx'
import { describeChange, orderChanges, priceChange } from '../logic/format.js'

// One alternative ("option") from the API Evaluation.
// audience "customer": friendly card with a choose button.
// audience "staff": adds route / score / similarity / staff notes and instructions.
export default function SubstitutionCard({ option, recommended, selected, audience, onChoose, chooseLabel, busy }) {
  const staff = audience === 'staff'
  const instructions = orderChanges(option.kitchen_instructions ?? [])
  return (
    <div className={`sub ${selected ? 'sub-selected' : ''} ${recommended ? 'sub-pending' : 'sub-done'}`}>
      <div className="row between">
        <strong>{option.label}</strong>
        <span className="row tight">
          {recommended && <span className="pill pill-recommended">Recommended</span>}
          {selected && staff && <span className="pill pill-approved">Current</span>}
        </span>
      </div>

      {option.changes?.length > 0 && (
        <div className="chips">{option.changes.map((c, i) => <ModChip key={i} chip={describeChange(c)} />)}</div>
      )}

      <div className={option.price_change_cents == null ? 'price danger' : 'price'}>{priceChange(option.price_change_cents)}</div>

      {staff && (
        <div className="meta">
          <span className={`pill pill-route-${option.route}`}>route: {option.route}</span>
          <span className="score">score {option.score == null ? '—' : option.score.toFixed(2)}</span>
          <span className="score">similarity {option.similarity == null ? '—' : option.similarity}</span>
        </div>
      )}

      {option.reasons?.length > 0 && (
        <ul className="why-list">{option.reasons.map((r, i) => <li key={i} className="why">{r}</li>)}</ul>
      )}

      {staff && option.staff_notes?.length > 0 && (
        <ul className="why-list notes">{option.staff_notes.map((r, i) => <li key={i}>{r}</li>)}</ul>
      )}

      {staff && instructions.length > 0 && (
        <details>
          <summary>Kitchen instructions</summary>
          <ul className="instructions">{instructions.map((l, i) => <li key={i}>{l}</li>)}</ul>
        </details>
      )}

      {onChoose && (
        <div className="row">
          <button className={recommended ? 'btn primary small' : 'btn small'} disabled={busy} onClick={() => onChoose(option.option_id)}>
            {chooseLabel ?? 'Choose this'}
          </button>
        </div>
      )}
    </div>
  )
}
