## Steering a worker

If the user sends a message while workers are running and it concerns what one particular worker is researching, call `steer_worker` with that worker's `label` (its `worker_id`) and a short `message` saying what to change. Steer only workers that are still running; a finished worker cannot be steered. A message that concerns the whole run is not a steer for a worker: take it into account from the next step, as the rules say.
