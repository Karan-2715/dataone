import { redirect } from "next/navigation";

export default function QueryStudioRedirect() {
  redirect("/dashboard/query-workspace?mode=sql");
}
