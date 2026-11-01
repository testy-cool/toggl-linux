import { Pencil, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemTitle,
} from "@/components/ui/item";
import { TimeInput } from "./TimeInput";
import { entryEnd, localTime } from "./model";
import { shortTime } from "./today";
import type { TodayRow } from "./today";
import type { Entry } from "./model";

export function TimeEntryRow({
  row,
  busy,
  edit,
  retime,
  resume,
}: {
  row: TodayRow;
  busy: boolean;
  edit: (entry: Entry) => void;
  retime: (entry: Entry, field: "start" | "stop", time: string) => void;
  resume?: (entry: Entry) => void;
}) {
  const entry = row.entry;
  const end = entryEnd(entry);
  return (
    <Item size="xs" className="interval-row">
      <ItemContent>
        <ItemTitle className="interval-times">
          <TimeInput
            key={`start:${entry.id}`}
            label={`Start of ${entry.description || "entry"}`}
            value={localTime(entry.start).slice(11, 16)}
            disabled={busy}
            onChange={(time) => retime(entry, "start", time)}
          />
          –
          {end ? (
            <TimeInput
              key={`stop:${entry.id}`}
              label={`End of ${entry.description || "entry"}`}
              value={localTime(end).slice(11, 16)}
              disabled={busy}
              onChange={(time) => retime(entry, "stop", time)}
            />
          ) : (
            "now"
          )}
          <span className="tabular-nums">{shortTime(row.seconds)}</span>
        </ItemTitle>
        <ItemDescription>
          {entry.description || "No description"}
          {entry._offline ? " · Pending sync" : ""}
        </ItemDescription>
      </ItemContent>
      <ItemActions>
        <Button
          variant="ghost"
          size="icon"
          disabled={busy}
          aria-label={`Edit entry ${entry.description || "No description"}`}
          onClick={(event) => {
            event.stopPropagation();
            edit(entry);
          }}
        >
          <Pencil />
        </Button>
        {resume && entry.stop && (
          <Button
            variant="ghost"
            size="icon"
            disabled={busy}
            aria-label={`Resume ${entry.description || "No description"}`}
            onClick={(event) => {
              event.stopPropagation();
              resume(entry);
            }}
          >
            <Play />
          </Button>
        )}
      </ItemActions>
    </Item>
  );
}
