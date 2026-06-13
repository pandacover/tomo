export type PendingApproval = {
  id: string;
  operation: string;
  target: string;
  reason: string;
  channelId?: string;
};