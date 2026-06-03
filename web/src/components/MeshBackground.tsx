import { motion } from 'motion/react'

export function MeshBackground() {
  return (
    <div className="fixed inset-0 -z-10 overflow-hidden pointer-events-none">
      {/* Primary violet orb */}
      <motion.div
        className="absolute w-[800px] h-[800px] rounded-full"
        style={{
          background: 'radial-gradient(circle, rgba(139, 92, 246, 0.10), transparent 60%)',
          left: '10%',
          top: '20%',
        }}
        animate={{
          x: [0, 60, -40, 20, 0],
          y: [0, -40, 50, -20, 0],
          scale: [1, 1.1, 0.95, 1.05, 1],
        }}
        transition={{ duration: 30, repeat: Infinity, ease: 'linear' }}
      />
      {/* Emerald orb */}
      <motion.div
        className="absolute w-[600px] h-[600px] rounded-full"
        style={{
          background: 'radial-gradient(circle, rgba(52, 211, 153, 0.07), transparent 60%)',
          right: '5%',
          top: '5%',
        }}
        animate={{
          x: [0, -50, 30, -20, 0],
          y: [0, 40, -30, 50, 0],
          scale: [1, 0.9, 1.1, 0.95, 1],
        }}
        transition={{ duration: 25, repeat: Infinity, ease: 'linear' }}
      />
      {/* Sky orb */}
      <motion.div
        className="absolute w-[500px] h-[500px] rounded-full"
        style={{
          background: 'radial-gradient(circle, rgba(56, 189, 248, 0.06), transparent 60%)',
          left: '45%',
          bottom: '5%',
        }}
        animate={{
          x: [0, 40, -50, 30, 0],
          y: [0, -50, 20, -30, 0],
        }}
        transition={{ duration: 22, repeat: Infinity, ease: 'linear' }}
      />
      {/* Rose accent orb */}
      <motion.div
        className="absolute w-[400px] h-[400px] rounded-full"
        style={{
          background: 'radial-gradient(circle, rgba(251, 113, 133, 0.05), transparent 60%)',
          right: '20%',
          bottom: '20%',
        }}
        animate={{
          x: [0, -30, 45, -15, 0],
          y: [0, 35, -25, 40, 0],
        }}
        transition={{ duration: 28, repeat: Infinity, ease: 'linear' }}
      />
    </div>
  )
}
