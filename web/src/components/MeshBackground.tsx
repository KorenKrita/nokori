import { motion } from 'motion/react'

export function MeshBackground() {
  return (
    <div className="fixed inset-0 -z-10 overflow-hidden pointer-events-none">
      <motion.div
        className="absolute w-[600px] h-[600px] rounded-full opacity-[0.035]"
        style={{
          background: 'radial-gradient(circle, #a78bfa, transparent 70%)',
          left: '15%',
          top: '30%',
        }}
        animate={{
          x: [0, 30, -20, 0],
          y: [0, -20, 30, 0],
        }}
        transition={{ duration: 20, repeat: Infinity, ease: 'linear' }}
      />
      <motion.div
        className="absolute w-[500px] h-[500px] rounded-full opacity-[0.025]"
        style={{
          background: 'radial-gradient(circle, #34d399, transparent 70%)',
          right: '10%',
          top: '10%',
        }}
        animate={{
          x: [0, -25, 15, 0],
          y: [0, 25, -15, 0],
        }}
        transition={{ duration: 25, repeat: Infinity, ease: 'linear' }}
      />
      <motion.div
        className="absolute w-[400px] h-[400px] rounded-full opacity-[0.02]"
        style={{
          background: 'radial-gradient(circle, #38bdf8, transparent 70%)',
          left: '50%',
          bottom: '10%',
        }}
        animate={{
          x: [0, 20, -30, 0],
          y: [0, -30, 10, 0],
        }}
        transition={{ duration: 18, repeat: Infinity, ease: 'linear' }}
      />
    </div>
  )
}
