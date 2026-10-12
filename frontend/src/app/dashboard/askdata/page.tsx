import { redirect } from "next/navigation";

export default function AskDataRedirect() {
  redirect("/dashboard/query-workspace?mode=ask");
}
