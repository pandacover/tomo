export type IntegrationKind = "skill" | "tool" | "gateway";

export type Integration = {
  id: string;
  name: string;
  kind: IntegrationKind;
  description: string;
  scopes: string[];
  enabled: boolean;
  reviewRequired?: boolean;
};