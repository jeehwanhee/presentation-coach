interface StepHeaderProps {
  step: 1 | 2 | 3;
  label: string;
}

const TOTAL_STEPS = 3;

export function StepHeader({ step, label }: StepHeaderProps) {
  return (
    <div className="step-header">
      <div className="brand">리허설 코치</div>
      <div className="step-pill">
        STEP <b>{step}</b> / {TOTAL_STEPS} · {label}
      </div>
    </div>
  );
}
