import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { Location } from "./types";

export function useLocations() {
  return useQuery({ queryKey: ["locations"], queryFn: () => api<Location[]>("/locations") });
}
