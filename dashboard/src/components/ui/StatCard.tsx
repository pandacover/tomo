import Link from "next/link";
import type { ReactNode } from "react";
import { Dot } from "@/components/ui/Dot";

type StatCardProps = {
  href: string;
  label: string;
  value: number | string;
  meta: ReactNode;
  dot?: "green" | "cyan" | "amber";
};

export function StatCard({ href, label, value, meta, dot = "green" }: StatCardProps) {
  return (
    <Link className="stat" href={href}>
      <span className="label">{label}</span>
      <span className="stat-value">{value}</span>
      <div className="status-row">
        <Dot variant={dot} />
        <span className="meta">{meta}</span>
      </div>
    </Link>
  );
}