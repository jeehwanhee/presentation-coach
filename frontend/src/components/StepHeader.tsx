interface StepHeaderProps {
  subtitle?: string;
}

export function StepHeader({ subtitle }: StepHeaderProps) {
  return (
    <div className="step-header">
      <span className="brand">
        PTPT
        {subtitle && ` - ${subtitle}`}
      </span>
    </div>
  );
}
