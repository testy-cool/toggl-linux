import { Pencil, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemTitle,
} from "@/components/ui/item";
import { clockRange, shortTime } from "./today";
import type { TodayRow } from "./today";
import type { Entry } from "./model";

export function TimeEntryRow({
  row,
  busy,
  edit,
  resume,
}: {
  row: TodayRow;
  busy: boolean;
  edit: (entry: Entry) => void;
  resume?: (entry: Entry) => void;
}) {
  const entry = row.entry;
  return (
    <Item size="xs" className="interval-row">
      <ItemContent>
        <ItemTitle>
          {clockRange(row)}{" "}
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
