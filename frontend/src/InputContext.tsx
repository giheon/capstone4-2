import { createContext, useContext } from "react";
import type { AppConfig } from "./types";
export const InputContext = createContext<AppConfig["inputs"] | null>(null);
export const useInputOptions = () => useContext(InputContext);
