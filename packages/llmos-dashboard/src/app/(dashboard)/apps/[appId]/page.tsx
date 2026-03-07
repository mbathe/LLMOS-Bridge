"use client";

import { useParams } from "next/navigation";
import { redirect } from "next/navigation";

export default function AppDetailRedirect() {
  const params = useParams();
  const appId = params.appId as string;
  redirect(`/applications/${appId}`);
}
