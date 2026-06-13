import { cn } from "@/lib/utils";

type DotVariant = "green" | "cyan" | "amber" | "red";

type DotProps = {
  variant?: DotVariant;
};

export function Dot({ variant }: DotProps) {
  return <span className={cn("dot", variant && variant !== "green" ? variant : undefined)} />;
}