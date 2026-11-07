# Restaurant AI Hackathon — Project Handoff

## Purpose of this document

This summarizes the current project direction for another ChatGPT instance and a four-person team. Continue from this concept rather than restarting broad ideation. No application, repository, technology stack, integrations, dataset, or final interface contracts have been established in this conversation. The architecture below is a proposed starting point, not an implemented system.

## Context and how the idea evolved

We are participating in the DoorDash × Nova hackathon. Its problem areas include preparation timing, packing/pickup, inventory/forecasting, bookkeeping, and staffing. We initially explored grouping similar orders, then shifted toward inventory and bookkeeping. We want a distinctive, useful application of AI that improves restaurant efficiency.

The current direction is helping restaurants accommodate special requests and propose ingredient substitutions. Preserve the inventory and financial connection; the project should do more than translate customer notes into kitchen instructions.

## Central product idea and latest clarification

Build an AI assistant that turns a special request into feasible modifications and recommends substitutions that are:

1. **Feasible:** compatible with current ingredients, preparation state, equipment, and restaurant-approved procedures.
2. **Close:** similar to the original ingredient or dish in culinary role, flavor, texture, portion, and the customer's stated intent.
3. **Beneficial for the restaurant:** considerate of ingredient cost, preparation effort, expected demand, potential shortages, surplus, and avoidable waste.

**The user's latest emphasis:** use previous ingredient usage when forecasting demand and evaluating substitutions. We interpret this as learning from historical ingredient usage to forecast future needs. Do not rank substitutions using current stock or purchase price alone.

A candidate can be in stock today yet be a poor recommendation if it is likely to be needed for popular dishes before the next delivery. Another candidate may be a strong choice because it meets the customer's needs and uses safe, usable surplus that demand forecasts suggest will remain unsold.

Customer requirements and operational limits are hard constraints. Business benefit ranks acceptable options; it must not override the customer's requirements or authorize an unapproved substitution.

## Illustrative scenario

A customer requests a replacement for chicken in a bowl. The chef has approved both mushrooms and tofu for that dish. Both are currently available.

- The forecast indicates tofu will be needed for expected dinner orders before replenishment.
- Mushrooms are expected to be surplus within their restaurant-defined usable window.
- Mushrooms are a reasonable culinary match for this particular dish and satisfy the stated request.

The assistant recommends mushrooms, explains the choice, shows any price and preparation changes, and asks the customer to accept. It may offer tofu as another acceptable option if sufficient stock can be preserved. If no option satisfies the constraints, it asks staff for help or explains that the request cannot currently be accommodated.

This is hypothetical. A chef must define appropriate substitutes; mushrooms and tofu are not interchangeable for every request, nutrition requirement, or dish.

## Intended workflow

1. Receive a selected dish and a natural-language request, such as removing an ingredient, substituting a protein, requesting an extra portion, or putting sauce on the side.
2. Interpret the request into structured constraints and modifications. Identify missing information or contradictions.
3. Retrieve recipes, component preparation states, approved alternatives, stock estimates, outstanding commitments, pricing, and historical usage.
4. Forecast ingredient needs through the relevant replenishment or usable-life horizon, with uncertainty.
5. Generate restaurant-approved candidates and filter out infeasible options.
6. Rank remaining options by culinary similarity, customer fit, and restaurant benefit.
7. Present a recommendation with reasons, alternatives, estimated price changes, and any preparation implications.
8. Obtain required customer acceptance and staff review.
9. Recheck availability on confirmation, save the accepted customized recipe, and update inventory once.
10. Send concise instructions to kitchen/packing staff and record actual outcomes where available.

## Preparation-aware feasibility

An ingredient being present does not mean it can be removed or substituted at every stage. For example, garlic already incorporated into a premixed sauce cannot simply be removed. A topping added individually can be omitted. Recipes should describe components, when ingredients are added, and chef-approved alternative preparations.

For explicit allergy requests, use verified ingredient information and restaurant cross-contact procedures, with staff confirmation. Do not infer safety from dish names or let a language model certify a preparation as allergen-free. Preserve uncertain requests and ask targeted questions.

## Forecasting and substitution logic

Useful initial inputs include timestamped dish/modifier sales, recipe quantities, ingredient usage or counts, deliveries, recorded waste, current stock, replenishment timing, and restaurant-defined usable windows. Separate measured usage from recipe-estimated consumption. Historical substitutions and stockouts can distort sales-derived demand; acknowledge these limitations.

A practical first version can estimate demand by ingredient and daypart using comparable historical periods, then evaluate forecast error on later held-out periods. Compare an AI/ML forecast with a simple historical baseline before claiming an improvement. Synthetic demo data must be clearly labeled.

For each candidate, consider stock after confirmed commitments, the requested portion, expected remaining demand, a buffer for uncertainty, and incoming deliveries. Deduct preparation and handling effort from any estimated economic benefit. Prevent recommendations from sending all customers toward the same surplus ingredient by updating reservations or commitments as orders are confirmed.

Use a transparent ranking scheme after hard filtering. Candidate factors include customer fit, culinary similarity, incremental ingredient cost, additional labor, shortage exposure, and avoidable waste. Exact weights remain undecided. Show multiple dimensions rather than implying one cheaper ingredient is automatically the best choice. Distinguish ingredient contribution estimates from full restaurant profit, and avoid double-counting waste savings.

## Where AI fits

- **Language interpretation:** extract modifications and constraints from varied customer wording; ask focused clarification questions.
- **Candidate matching:** help match ingredients by culinary role using the restaurant's approved options and verified descriptions.
- **Forecasting:** predict ingredient needs from historical usage and relevant operating conditions.
- **Explanation:** express the selected option and tradeoffs using the actual evaluation results.

Use explicit, reproducible calculations for quantities, pricing, and stock updates. Keep feasibility constraints and financial arithmetic outside unconstrained model generation.

## Four-person division of work

| Person | Owned folders | Responsibilities |
| --- | --- | --- |
| 1 — Frontend | `frontend/` | Customer request flow, ranked alternatives and explanations, acceptance, and staff ticket view; begin with fixture responses. |
| 2 — AI interpretation and culinary matching | `backend/ai/` | Structured request extraction, ambiguity handling, candidate similarity among approved substitutions, and grounded explanation generation. |
| 3 — Forecasting and decision engine | `backend/engine/` | Historical-usage forecast, feasibility filtering, candidate ranking, inventory deltas, price/cost calculations, and explanations of decision factors. |
| 4 — Data and API integration | `backend/api/`, `backend/data/` | Sample restaurant/history, validation, endpoints, orchestration, persistence, confirmations, and inventory updates. Maintain agreed contracts and shared backend setup. |

This updates the earlier split to include forecasting explicitly. Person 3 has the largest analytical scope: use a simple forecast initially, then improve it once the full flow works. Person 4 prepares the historical dataset so forecasting can develop independently.

Proposed repository layout:

```text
frontend/
backend/
  ai/
  engine/
  api/
  data/
contracts/
fixtures/
README.md
```

## Proposed interfaces

These need team agreement before implementation; they are not finalized.

- `GET /menu`: menu and supported customization information.
- `POST /evaluate`: selected dish, request, and clarification answers; returns a ranked evaluation.
- `POST /confirm`: accepted evaluation/version and option; rechecks availability and applies the order once.
- `GET /orders`: confirmed customized tickets for staff.

Suggested evaluation fields: `evaluation_id`, `version`, `status`, `interpreted_modifications`, `clarification_questions`, `ranked_options`, `requires_staff_review`, and `reasons`.

Each ranked option should identify its modifications, similarity rationale, feasibility evidence, forecast-related tradeoffs, price change, ingredient cost change, complete ingredient requirements, differences from the standard recipe, and kitchen/packing instructions. Mark estimated values and uncertain stock information clearly.

Proposed statuses: `ready`, `needs_clarification`, `needs_customer_approval`, `needs_staff_review`, and `unavailable`. Agree on how an option requiring multiple actions is represented; flags may supplement a single status.

## Agreements needed before separate development

- Choose the stack, package managers, startup commands, ports, and environment-variable names. Keep API keys on the backend and out of git.
- Agree on stable dish/ingredient IDs, time zones, canonical quantity units, integer cents, and forecast horizons.
- Define the recipe/preparation schema and distinguish raw, prepared, committed, and available stock.
- Clearly separate whole customized-recipe requirements from changes relative to the original recipe. Avoid double deductions.
- Agree on the AI output, forecast input/output, engine input/output, and API responses using one complete shared example.
- Decide who approves substitutions, how customers accept alternatives, how uncertainty is shown, and how stale evaluations are handled.
- Save contracts in `contracts/` and example requests/responses in `fixtures/`.
- Use feature branches, folder ownership, small pull requests, and one owner for shared dependency/configuration files.

## Hackathon scope and demonstration

Start with one restaurant, five dishes, a small ingredient catalog, chef-approved substitution pairs, and labeled historical data. Simulate order channels and pickup information unless supported integration access is provided. No real DoorDash API access has been established.

Build one complete path early: a request produces an option, the customer accepts, staff receive instructions, and stock changes correctly. Then demonstrate:

1. A straightforward request with an available close substitute.
2. A modification blocked by a premixed component.
3. Two feasible substitutes where historical usage forecasts favor one to protect future demand or use surplus.
4. A stock change that requires reevaluation before confirmation.

Evaluate handling time, clarification burden, modification accuracy, forecast error, feasibility violations, and simulated shortage/waste outcomes. Compare with manual handling and simple substitution/forecast baselines. Do not claim actual profit or waste reductions from a synthetic demonstration.

## Decisions still open

Project name, team member assignments, stack, repository URL, concrete dataset, data access, forecast model, similarity representation, ranking weights, usable-life tracking, and final schemas remain undecided. The next assistant should help make these concrete while preserving the core goal: feasible, close substitutions informed by historical ingredient usage and beneficial restaurant outcomes.

## Background references from the earlier discussion

These establish existing capabilities, not uniqueness of this proposed product:

- DoorDash special instructions: https://help.doordash.com/en-us/consumers/article/can-i-specify-special-instructions-for-my-order
- Toast API special requests: https://doc.toasttab.com/doc/devguide/apiSpecifyingModifiersAndInstructions.html
- MarginEdge theoretical usage: https://help.marginedge.com/hc/en-us/articles/360015329433-Getting-Started-with-Theoretical-Usage-Reporting

No exhaustive competitor or novelty review has been performed. Focus the project on its specific forecasting-informed substitution workflow rather than claiming customization or forecasting itself is new.
