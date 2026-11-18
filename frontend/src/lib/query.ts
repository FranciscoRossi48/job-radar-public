import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 20_000,
      refetchOnWindowFocus: false,
      retry: (failures, error) => {
        const status =
          typeof error === "object" && error !== null && "status" in error
            ? Number(error.status)
            : 500;
        return status >= 500 && failures < 2;
      },
    },
    mutations: { retry: false },
  },
});
