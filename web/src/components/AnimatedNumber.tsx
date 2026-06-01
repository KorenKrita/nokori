import { useEffect, useRef, useState } from 'react'
import { motion, useMotionValue, useTransform, animate } from 'motion/react'

interface AnimatedNumberProps {
  value: number
  className?: string
}

export function AnimatedNumber({ value, className }: AnimatedNumberProps) {
  const motionValue = useMotionValue(0)
  const rounded = useTransform(motionValue, (v) => Math.round(v))
  const [display, setDisplay] = useState(0)

  useEffect(() => {
    const controls = animate(motionValue, value, {
      duration: 1.2,
      ease: [0.32, 0.72, 0, 1],
    })
    return () => controls.stop()
  }, [value, motionValue])

  useEffect(() => {
    const unsubscribe = rounded.on('change', (v) => setDisplay(v))
    return () => unsubscribe()
  }, [rounded])

  return (
    <motion.span
      className={className}
      initial={{ opacity: 0, scale: 0.5, filter: 'blur(8px)' }}
      animate={{ opacity: 1, scale: 1, filter: 'blur(0px)' }}
      transition={{ duration: 0.8, ease: [0.32, 0.72, 0, 1] }}
    >
      {display}
    </motion.span>
  )
}
