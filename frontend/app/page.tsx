import { CareerWorkspace } from "@/components/workspace";

export default function Home() {
  return <CareerWorkspace langfuse={process.env.NEXT_PUBLIC_LANGFUSE_URL ?? ""} />;
}
