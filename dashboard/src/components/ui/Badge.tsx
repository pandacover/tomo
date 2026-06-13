import type { ReactNode } from "react";
import { Dot } from "@/components/ui/Dot";

type BadgeProps = {
  children: ReactNode;
  dot?: "green" | "cyan" | "amber" | "red";
};

export function Badge({ children, dot }: BadgeProps) {
  return (
    <span className="badge">
      {dot ? <Dot variant={dot} /> : null}
      {children}
    </span>
  );
}