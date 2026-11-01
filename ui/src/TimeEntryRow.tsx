import { useLayoutEffect, useRef, useState } from "react";
import { Pencil, Play } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemTitle,
} from "@/components/ui/item";
import { TimeInput } from "./TimeInput";
import { ActionHint } from "./ActionHint";
import { entryEnd, localTime } from "./model";
import { clockRange, shortTime } from "./today";
import type { TodayRow } from "./today";
import type { Entry } from "./model";

export function TimeEntryRow({
  row,
  parentLabel,
  busy,
  savingTime = false,
  focusRange = false,
  onRangeFocused,
  edit,
  retime,
  resume,
}: {
  row: TodayRow;
  parentLabel?: string;
  busy: boolean;
  savingTime?: boolean;
  focusRange?: boolean;
  onRangeFocused?: () => void;
  edit: (entry: Entry) => void;
  retime: (entry: Entry, field: "start" | "stop", time: string) => void;
  resume?: (entry: Entry) => void;
}) {
  const entry = row.entry;
  const end = entryEnd(entry);
  const [editingTime, setEditingTime] = useState(false);
  const rangeRef = useRef<HTMLButtonElement>(null);
  const startRef = useRef<HTMLInputElement>(null);
  const restoreRangeFocus = useRef(false);
  const handledFocusRequest = useRef(false);
  useLayoutEffect(() => {
    if (editingTime) startRef.current?.focus();
    else if (restoreRangeFocus.current) {
      restoreRangeFocus.current = false;
      rangeRef.current?.focus();
    }
  }, [editingTime]);
  useLayoutEffect(() => {
    if (focusRange && !editingTime && !handledFocusRequest.current) {
      handledFocusRequest.current = true;
      rangeRef.current?.focus();
      onRangeFocused?.();
    }
    if (!focusRange) handledFocusRequest.current = false;
  }, [editingTime, focusRange, onRangeFocused]);
  const nested = parentLabel !== undefined;
  const description = nested
    ? entry.description?.trim() &&
      entry.description.trim() !== parentLabel.trim()
      ? entry.description
      : ""
    : entry.description || "No description";
  return (
    <Item
      size="xs"
      data-editing-time={editingTime || undefined}
      className={cn("interval-row px-0 py-1", nested && "ps-7 py-0.5 gap-1")}
    >
      <ItemContent>
        <ItemTitle className="interval-times">
          {editingTime ? (
            <div className="interval-time-fields">
              <TimeInput
                key={`start:${entry.id}`}
                label={`Start of ${entry.description || "entry"}`}
                value={localTime(entry.start).slice(11, 16)}
                disabled={busy && !savingTime}
                inputRef={startRef}
                onChange={(time) => retime(entry, "start", time)}
              />
              <span aria-hidden="true">–</span>
              {end ? (
                <TimeInput
                  key={`stop:${entry.id}`}
                  label={`End of ${entry.description || "entry"}`}
                  value={localTime(end).slice(11, 16)}
                  disabled={busy && !savingTime}
                  onChange={(time) => retime(entry, "stop", time)}
                />
              ) : (
                <span>now</span>
              )}
              <Button
                variant="ghost"
                size="xs"
                disabled={busy && !savingTime}
                onClick={(event) => {
                  event.stopPropagation();
                  restoreRangeFocus.current = true;
                  setEditingTime(false);
                }}
              >
                Done
              </Button>
            </div>
          ) : (
            <Button
              ref={rangeRef}
              variant="ghost"
              size="xs"
              className="interval-range"
              disabled={busy && !savingTime}
              aria-label={`Correct times for ${entry.description || "entry"}`}
              onClick={(event) => {
                event.stopPropagation();
                setEditingTime(true);
              }}
            >
              {clockRange(row)}
            </Button>
          )}
        </ItemTitle>
        {description && <ItemDescription>{description}</ItemDescription>}
        {entry._offline && <ItemDescription>Pending sync</ItemDescription>}
      </ItemContent>
      <ItemActions>
        <ActionHint text="Edit this time entry">
          <Button
            variant="ghost"
            size="icon-sm"
            disabled={busy}
            aria-label={`Edit entry ${entry.description || "No description"}`}
            onClick={(event) => {
              event.stopPropagation();
              edit(entry);
            }}
          >
            <Pencil />
          </Button>
        </ActionHint>
        {resume && entry.stop && (
          <ActionHint text="Resume this entry and stop any current timer">
            <Button
              variant="ghost"
              size="icon-sm"
              disabled={busy}
              aria-label={`Resume ${entry.description || "No description"}`}
              onClick={(event) => {
                event.stopPropagation();
                resume(entry);
              }}
            >
              <Play />
            </Button>
          </ActionHint>
        )}
      </ItemActions>
      <span className="work-time text-muted-foreground">
        {shortTime(row.seconds)}
      </span>
    </Item>
  );
}
