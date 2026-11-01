import { useState, type KeyboardEvent, type MouseEvent } from "react";
import { cn } from "cn";
import { TimePicker, TimePickerInput } from "@/components/reui/time-picker";
import { Input } from "@/components/ui/input";
import { Field, FieldLabel } from "@/components/ui/field";
import { isDay } from "./model";

/* Keys stay in the field so the tree cannot take them; Escape with nothing to undo still closes the window. */
function keepKeys(event: KeyboardEvent) {
  if (event.key !== "Escape" || event.defaultPrevented) event.stopPropagation();
}

/* The first click selects the whole time, so typing replaces it. */
function selectOnFirstClick(event: MouseEvent<HTMLInputElement>) {
  const field = event.currentTarget;
  if (document.activeElement === field || field.disabled) return;
  event.preventDefault();
  field.focus();
  field.select();
}

/* Each committed time is a save, so an arrow key must not write one per press. */
function noArrowStepping(event: KeyboardEvent) {
  if (event.key === "ArrowUp" || event.key === "ArrowDown")
    event.preventDefault();
}

/** Type 930, 14:30 or 2:30 pm. Enter or leaving the field applies it. */
export function TimeInput({
  id,
  label,
  value,
  disabled,
  className,
  onChange,
}: {
  id?: string;
  label: string;
  value: string;
  disabled?: boolean;
  className?: string;
  onChange(value: string): void;
}) {
  return (
    <div
      className={cn("time-input", className)}
      onClick={(event) => event.stopPropagation()}
      onKeyDown={keepKeys}
    >
      <TimePicker
        id={id}
        aria-label={label}
        value={value}
        disabled={disabled}
        onValueChange={(next) => {
          if (next && next !== value) onChange(next);
        }}
      >
        <TimePickerInput
          onMouseDown={selectOnFirstClick}
          onFocus={(event) => event.currentTarget.select()}
          onKeyDown={noArrowStepping}
        />
      </TimePicker>
    </div>
  );
}

/** A typed time and date with no calendar in the way; `value` is local YYYY-MM-DDTHH:mm:ss. */
export function DateTimeField({
  id,
  label,
  value,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  disabled?: boolean;
  onChange(value: string): void;
}) {
  const day = value.slice(0, 10);
  const [typed, setTyped] = useState<string | null>(null);
  const invalid = typed !== null && !isDay(typed);
  return (
    <Field>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <div className="date-time-field">
        <TimeInput
          id={id}
          label={label}
          value={value.slice(11, 16)}
          disabled={disabled}
          onChange={(time) => onChange(`${day}T${time}:00`)}
        />
        <Input
          aria-label={`${label} date`}
          aria-invalid={invalid || undefined}
          placeholder="YYYY-MM-DD"
          inputMode="numeric"
          value={typed ?? day}
          disabled={disabled}
          onChange={(event) => {
            setTyped(event.target.value);
            if (isDay(event.target.value))
              onChange(`${event.target.value}${value.slice(10)}`);
          }}
          onBlur={() => setTyped(null)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && invalid) event.preventDefault();
          }}
        />
      </div>
    </Field>
  );
}
