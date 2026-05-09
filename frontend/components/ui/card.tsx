"use client"

import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

const cardVariants = cva(
    "rounded-xl border bg-card text-card-foreground shadow",
    {
        variants: {
            variant: {
                default: "",
                interactive: [
                    "cursor-pointer outline-none",
                    "transition-[transform,opacity,box-shadow,colors,border-color] duration-200 ease-[cubic-bezier(0.2,0,0,1)]",
                    "hover:shadow-md hover:border-border/80",
                    "active:scale-[0.96]",
                    "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                ].join(" "),
                inset: "rounded-lg shadow-none",
                hero: "rounded-2xl shadow-md",
            },
            surface: {
                elevated: "",
                outlined: "shadow-none border-2",
            },
        },
        defaultVariants: {
            variant: "default",
            surface: "elevated",
        },
    }
)

export interface CardProps
    extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof cardVariants> { }

const Card = React.forwardRef<HTMLDivElement, CardProps>(
    ({ className, variant, surface, ...props }, ref) => (
        <div
            ref={ref}
            className={cn(cardVariants({ variant, surface }), className)}
            {...props}
        />
    )
)
Card.displayName = "Card"

const CardHeader = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
    ({ className, ...props }, ref) => (
        <div ref={ref} className={cn("flex flex-col space-y-1.5 p-6", className)} {...props} />
    )
)
CardHeader.displayName = "CardHeader"

const CardTitle = React.forwardRef<HTMLParagraphElement, React.HTMLAttributes<HTMLHeadingElement>>(
    ({ className, ...props }, ref) => (
        <h3 ref={ref} className={cn("font-semibold leading-none tracking-tight", className)} {...props} />
    )
)
CardTitle.displayName = "CardTitle"

const CardDescription = React.forwardRef<HTMLParagraphElement, React.HTMLAttributes<HTMLParagraphElement>>(
    ({ className, ...props }, ref) => (
        <p ref={ref} className={cn("text-sm text-muted-foreground", className)} {...props} />
    )
)
CardDescription.displayName = "CardDescription"

const CardContent = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
    ({ className, ...props }, ref) => (
        <div ref={ref} className={cn("p-6 pt-0", className)} {...props} />
    )
)
CardContent.displayName = "CardContent"

const CardFooter = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
    ({ className, ...props }, ref) => (
        <div ref={ref} className={cn("flex items-center p-6 pt-0", className)} {...props} />
    )
)
CardFooter.displayName = "CardFooter"

export {
    Card,
    CardHeader,
    CardFooter,
    CardTitle,
    CardDescription,
    CardContent,
    cardVariants,
}
