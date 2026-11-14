import type { ButtonHTMLAttributes } from "react";
import { Link, type LinkProps } from "react-router-dom";

import styles from "./Button.module.css";

type Variant = "primary" | "secondary";

interface CommonProps {
  variant?: Variant;
}

type ButtonProps = CommonProps & ButtonHTMLAttributes<HTMLButtonElement>;
type ButtonLinkProps = CommonProps & LinkProps;

export function Button({ variant = "primary", className, ...rest }: ButtonProps) {
  return <button className={cx(styles.button, styles[variant], className)} {...rest} />;
}

export function ButtonLink({ variant = "primary", className, ...rest }: ButtonLinkProps) {
  return <Link className={cx(styles.button, styles[variant], className)} {...rest} />;
}

function cx(...parts: Array<string | undefined>) {
  return parts.filter(Boolean).join(" ");
}
