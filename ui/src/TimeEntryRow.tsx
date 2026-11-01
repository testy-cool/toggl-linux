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
import { shortTime } from "./today";
import type { TodayRow } from "./today";
import type { Entry } from "./model";

export function TimeEntryRow({
  row,
  parentLabel,
  busy,
  edit,
  retime,
  resume,
}: {
  row: TodayRow;
  parentLabel?: string;
  busy: boolean;
  edit: (entry: Entry) => void;
  retime: (entry: Entry, field: "start" | "stop", time: string) => void;
  resume?: (entry: Entry) => void;
}) {
  const entry = row.entry;
  const end = entryEnd(entry);
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
      className={cn("interval-row px-0 py-1", nested && "ps-7 py-0.5 gap-1")}
    >
      <ItemContent>
        <ItemTitle className="interval-times">
          <TimeInput
            key={`start:${entry.id}`}
            label={`Start of ${entry.description || "entry"}`}
            value={localTime(entry.start).slice(11, 16)}
            onChange={(time) => retime(entry, "start", time)}
          />
          –
          {end ? (
            <TimeInput
              key={`stop:${entry.id}`}
              label={`End of ${entry.description || "entry"}`}
              value={localTime(end).slice(11, 16)}
              onChange={(time) => retime(entry, "stop", time)}
            />
          ) : (
            "now"
          )}
        </ItemTitle>
        {(description || entry._offline) && (
          <ItemDescription>
            {description}
            {entry._offline ? `${description ? " · " : ""}Pending sync` : ""}
          </ItemDescription>
        )}
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
      <span
        className="work-time text-muted-foreground"
        aria-label="Time today for this interval"
      >
        {shortTime(row.seconds)}
      </span>
    </Item>
  );
}
