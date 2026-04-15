$begin 'Profile'
	$begin 'ProfileGroup'
		MajorVer=2025
		MinorVer=2
		Name='Solution Process'
		$begin 'StartInfo'
			I(1, 'Start Time', '04/13/2026 17:03:26')
			I(1, 'Host', 'RISHABHLAPTOP')
			I(1, 'Processor', '20')
			I(1, 'OS', 'NT 10.0')
			I(1, 'Product', 'HFSS Version 2025.2.0')
		$end 'StartInfo'
		$begin 'TotalInfo'
			I(1, 'Elapsed Time', '00:02:21')
			I(1, 'ComEngine Memory', '113 M')
		$end 'TotalInfo'
		GroupOptions=8
		TaskDataOptions('CPU Time'=8, Memory=8, 'Real Time'=8)
		ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 1, \'Executing From\', \'C:\\\\Program Files\\\\ANSYS Inc\\\\ANSYS Student\\\\v252\\\\AnsysEM\\\\HFSSCOMENGINE.exe\')', false, true)
		$begin 'ProfileGroup'
			MajorVer=2025
			MinorVer=2
			Name='HPC'
			$begin 'StartInfo'
				I(1, 'Type', 'Auto')
				I(1, 'MPI Vendor', 'Intel')
				I(1, 'MPI Version', '2021')
			$end 'StartInfo'
			$begin 'TotalInfo'
				I(0, ' ')
			$end 'TotalInfo'
			GroupOptions=0
			TaskDataOptions(Memory=8)
			ProfileItem('Machine', 0, 0, 0, 0, 0, 'I(5, 1, \'Name\', \'RishabhLaptop\', 1, \'Memory\', \'31.7 GB\', 3, \'RAM Limit\', 90, \'%f%%\', 2, \'Cores\', 4, false, 1, \'Free Disk Space\', \'102 GB\')', false, true)
		$end 'ProfileGroup'
		ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 1, \'Allow off core\', \'True\')', false, true)
		ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 1, \'Solution Basis Order\', \'1\')', false, true)
		ProfileItem('Design Validation', 0, 0, 0, 0, 0, 'I(1, 0, \'Elapsed time : 00:00:00 , HFSS ComEngine Memory : 104 M\')', false, true)
		ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Perform full validations with standard port validations\')', false, true)
		ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
		$begin 'ProfileGroup'
			MajorVer=2025
			MinorVer=2
			Name='Initial Meshing'
			$begin 'StartInfo'
				I(1, 'Time', '04/13/2026 17:03:26')
			$end 'StartInfo'
			$begin 'TotalInfo'
				I(1, 'Elapsed Time', '00:00:06')
			$end 'TotalInfo'
			GroupOptions=4
			TaskDataOptions('CPU Time'=8, Memory=8, 'Real Time'=8)
			ProfileItem('Mesh', 1, 0, 1, 0, 61268, 'I(3, 1, \'Type\', \'Phi\', 2, \'Tetrahedra\', 7101, false, 0, \'Includes 0 sec to protect layer data\')', true, true)
			ProfileItem('Post', 1, 0, 1, 0, 72612, 'I(1, 2, \'Tetrahedra\', 9467, false)', true, true)
			ProfileItem('Lambda Refine', 0, 0, 0, 0, 31936, 'I(1, 2, \'Tetrahedra\', 9592, false)', true, true)
			ProfileItem('Simulation Setup', 0, 0, 0, 0, 195056, 'I(2, 2, \'Tetrahedra\', 6469, false, 1, \'Disk\', \'0 Bytes\')', true, true)
			ProfileItem('Port Adapt', 0, 0, 0, 0, 203904, 'I(2, 2, \'Tetrahedra\', 6469, false, 1, \'Disk\', \'34.6 KB\')', true, true)
			ProfileItem('Port Refine', 0, 0, 0, 0, 32048, 'I(1, 2, \'Tetrahedra\', 9642, false)', true, true)
		$end 'ProfileGroup'
		$begin 'ProfileGroup'
			MajorVer=2025
			MinorVer=2
			Name='Adaptive Meshing'
			$begin 'StartInfo'
				I(1, 'Time', '04/13/2026 17:03:33')
			$end 'StartInfo'
			$begin 'TotalInfo'
				I(1, 'Elapsed Time', '00:00:34')
			$end 'TotalInfo'
			GroupOptions=4
			TaskDataOptions('CPU Time'=8, Memory=8, 'Real Time'=8)
			$begin 'ProfileGroup'
				MajorVer=2025
				MinorVer=2
				Name='Adaptive Pass 1'
				$begin 'StartInfo'
					I(0, 'Adaptive Meshing')
				$end 'StartInfo'
				$begin 'TotalInfo'
					I(0, ' ')
				$end 'TotalInfo'
				GroupOptions=0
				TaskDataOptions('CPU Time'=8, Memory=8, 'Real Time'=8)
				ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2.5GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:01')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 196980, 'I(2, 2, \'Tetrahedra\', 6519, false, 1, \'Disk\', \'0 Bytes\')', true, true)
					ProfileItem('Matrix Assembly', 0, 0, 0, 0, 259584, 'I(3, 2, \'Tetrahedra\', 6519, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'17 Bytes\')', true, true)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Direct Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 0, 0, 1, 0, 381536, 'I(5, 1, \'Type\', \'DCS\', 2, \'Cores\', 4, false, 2, \'Matrix size\', 44015, false, 3, \'Matrix bandwidth\', 19.3737, \'%5.1f\', 1, \'Disk\', \'174 KB\')', true, true)
					ProfileItem('Field Recovery', 0, 0, 0, 0, 381536, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'2.88 MB\')', true, true)
					$begin 'ProfileGroup'
						MajorVer=2025
						MinorVer=2
						Name='APIPms1'
						$begin 'StartInfo'
							I(1, 'Timesinceepock', '1776114218')
						$end 'StartInfo'
						$begin 'TotalInfo'
							I(0, ' ')
						$end 'TotalInfo'
						GroupOptions=16
						TaskDataOptions('CPU Time'=8, 'Real Time'=8)
						ProfileItem('solverinfo', 0, 0, 0, 0, 0, 'I(10, 1, \'Solvertype\', \'shared_memory\', 1, \'Precision\', \'double\', 1, \'Solversymmetry\', \'complex_sym\', 1, \'Matrixdim\', \'44015\', 1, \'Matrixbw\', \'19.384001\', 1, \'Matrixnnz\', \'853186\', 1, \'Rootdim\', \'255\', 1, \'Mathtype\', \'mkl\', 1, \'Mpitasks\', \'1\', 1, \'Threadspertasks\', \'0\')', false, true)
						ProfileItem('sysinfo', 0, 0, 0, 0, 0, 'I(12, 1, \'Os\', \'win\', 1, \'Cpuid\', \'12th Gen Intel(R) Core(TM) i7-12800H\', 1, \'CpuPhysicCores\', \'14\', 1, \'CpuLogicCores\', \'20\', 1, \'Cpufreqkhz\', \'28771500032.000000\', 1, \'Cpucachelinesizebytes\', \'64\', 1, \'Cpuestlastlevelcachesizemb\', \'25.000000\', 1, \'Cpuestgflops\', \'409.600006\', 1, \'Memorybwestkbps\', \'76.800003\', 1, \'Numanodes\', \'1\', 1, \'Virtualmemkb\', \'137439000000.000000\', 1, \'Pagesizekb\', \'4096\')', false, true)
						ProfileItem('analysisinfo', 0, 0, 0, 0, 0, 'I(9, 1, \'Analysisstatus\', \'valid\', 1, \'Numsupernodes\', \'3890\', 1, \'Factornnz\', \'6320654\', 1, \'Factorestflops\', \'3653140000\', 1, \'Fbsestflops\', \'20175653\', 1, \'Rootfactestflops\', \'5528319\', 1, \'Rootfbsestflops\', \'32512\', 1, \'Analysistimesec\', \'0.300889\', 1, \'Analysismemkb\', \'24280.000000\')', false, true)
						ProfileItem('factorinfo', 0, 0, 0, 0, 0, 'I(4, 1, \'Fatorizationstatus\', \'valid\', 1, \'Factorizationnumcores\', \'4\', 1, \'Factorizationtimesec\', \'0.273314\', 1, \'Factorizationmentotalkb\', \'149171.000000\')', false, true)
						ProfileItem('fbsinfo', 0, 0, 0, 0, 0, 'I(10, 1, \'Fbstatus\', \'valid\', 1, \'Fbstype\', \'fullsolve\', 1, \'Fbsmt\', \'false\', 1, \'Fbsmrhs\', \'false\', 1, \'Fbsnumcores\', \'4\', 1, \'Fbsnumsolvestotal\', \'1\', 1, \'Fbsnumsolves\', \'1\', 1, \'Fbsavgsolvetime1solvesec\', \'0.016783\', 1, \'Fbscputimesec\', \'0.016783\', 1, \'Fbsmemorytotalkb\', \'143152.000000\')', false, true)
						ProfileItem('solverprofile', 0, 0, 0, 0, 0, 'I(2, 1, \'Maxmemkb\', \'149171\', 1, \'Maxdiskkb\', \'0\')', false, true)
					$end 'ProfileGroup'
				$end 'ProfileGroup'
				ProfileItem('Data Transfer', 0, 0, 0, 0, 109132, 'I(1, 0, \'Adaptive Pass 1\')', true, true)
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
			$end 'ProfileGroup'
			$begin 'ProfileGroup'
				MajorVer=2025
				MinorVer=2
				Name='Adaptive Pass 2'
				$begin 'StartInfo'
					I(0, 'Adaptive Meshing')
				$end 'StartInfo'
				$begin 'TotalInfo'
					I(0, ' ')
				$end 'TotalInfo'
				GroupOptions=0
				TaskDataOptions('CPU Time'=8, Memory=8, 'Real Time'=8)
				ProfileItem('Adaptive Refine', 0, 0, 0, 0, 39128, 'I(1, 2, \'Tetrahedra\', 12243, false)', true, true)
				ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2.5GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:02')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 202728, 'I(2, 2, \'Tetrahedra\', 9000, false, 1, \'Disk\', \'0 Bytes\')', true, true)
					ProfileItem('Matrix Assembly', 0, 0, 1, 0, 288224, 'I(3, 2, \'Tetrahedra\', 9000, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'10 Bytes\')', true, true)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Direct Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 1, 0, 2, 0, 497432, 'I(5, 1, \'Type\', \'DCS\', 2, \'Cores\', 4, false, 2, \'Matrix size\', 59283, false, 3, \'Matrix bandwidth\', 19.8046, \'%5.1f\', 1, \'Disk\', \'233 KB\')', true, true)
					ProfileItem('Field Recovery', 0, 0, 0, 0, 497432, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'855 KB\')', true, true)
					$begin 'ProfileGroup'
						MajorVer=2025
						MinorVer=2
						Name='APIPms1'
						$begin 'StartInfo'
							I(1, 'Timesinceepock', '1776114222')
						$end 'StartInfo'
						$begin 'TotalInfo'
							I(0, ' ')
						$end 'TotalInfo'
						GroupOptions=16
						TaskDataOptions('CPU Time'=8, 'Real Time'=8)
						ProfileItem('solverinfo', 0, 0, 0, 0, 0, 'I(10, 1, \'Solvertype\', \'shared_memory\', 1, \'Precision\', \'double\', 1, \'Solversymmetry\', \'complex_sym\', 1, \'Matrixdim\', \'59283\', 1, \'Matrixbw\', \'19.813900\', 1, \'Matrixnnz\', \'1174626\', 1, \'Rootdim\', \'431\', 1, \'Mathtype\', \'mkl\', 1, \'Mpitasks\', \'1\', 1, \'Threadspertasks\', \'0\')', false, true)
						ProfileItem('sysinfo', 0, 0, 0, 0, 0, 'I(12, 1, \'Os\', \'win\', 1, \'Cpuid\', \'12th Gen Intel(R) Core(TM) i7-12800H\', 1, \'CpuPhysicCores\', \'14\', 1, \'CpuLogicCores\', \'20\', 1, \'Cpufreqkhz\', \'28748699648.000000\', 1, \'Cpucachelinesizebytes\', \'64\', 1, \'Cpuestlastlevelcachesizemb\', \'25.000000\', 1, \'Cpuestgflops\', \'409.600006\', 1, \'Memorybwestkbps\', \'76.800003\', 1, \'Numanodes\', \'1\', 1, \'Virtualmemkb\', \'137439000000.000000\', 1, \'Pagesizekb\', \'4096\')', false, true)
						ProfileItem('analysisinfo', 0, 0, 0, 0, 0, 'I(9, 1, \'Analysisstatus\', \'valid\', 1, \'Numsupernodes\', \'5250\', 1, \'Factornnz\', \'11006364\', 1, \'Factorestflops\', \'9510220000\', 1, \'Fbsestflops\', \'36090501\', 1, \'Rootfactestflops\', \'26689973\', 1, \'Rootfbsestflops\', \'92880\', 1, \'Analysistimesec\', \'0.451402\', 1, \'Analysismemkb\', \'33164.000000\')', false, true)
						ProfileItem('factorinfo', 0, 0, 0, 0, 0, 'I(4, 1, \'Fatorizationstatus\', \'valid\', 1, \'Factorizationnumcores\', \'4\', 1, \'Factorizationtimesec\', \'0.556263\', 1, \'Factorizationmentotalkb\', \'263036.000000\')', false, true)
						ProfileItem('fbsinfo', 0, 0, 0, 0, 0, 'I(10, 1, \'Fbstatus\', \'valid\', 1, \'Fbstype\', \'fullsolve\', 1, \'Fbsmt\', \'false\', 1, \'Fbsmrhs\', \'false\', 1, \'Fbsnumcores\', \'4\', 1, \'Fbsnumsolvestotal\', \'1\', 1, \'Fbsnumsolves\', \'1\', 1, \'Fbsavgsolvetime1solvesec\', \'0.025106\', 1, \'Fbscputimesec\', \'0.025106\', 1, \'Fbsmemorytotalkb\', \'239720.000000\')', false, true)
						ProfileItem('solverprofile', 0, 0, 0, 0, 0, 'I(2, 1, \'Maxmemkb\', \'263036\', 1, \'Maxdiskkb\', \'0\')', false, true)
					$end 'ProfileGroup'
				$end 'ProfileGroup'
				ProfileItem('Data Transfer', 0, 0, 0, 0, 110008, 'I(1, 0, \'Adaptive Pass 2\')', true, true)
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
				ProfileFootnote('I(1, 3, \'Max Mag. Delta S\', 0.615753, \'%.5f\')', 0)
			$end 'ProfileGroup'
			$begin 'ProfileGroup'
				MajorVer=2025
				MinorVer=2
				Name='Adaptive Pass 3'
				$begin 'StartInfo'
					I(0, 'Adaptive Meshing')
				$end 'StartInfo'
				$begin 'TotalInfo'
					I(0, ' ')
				$end 'TotalInfo'
				GroupOptions=0
				TaskDataOptions('CPU Time'=8, Memory=8, 'Real Time'=8)
				ProfileItem('Adaptive Refine', 1, 0, 1, 0, 45556, 'I(1, 2, \'Tetrahedra\', 16275, false)', true, true)
				ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2.5GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:03')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 211168, 'I(2, 2, \'Tetrahedra\', 12806, false, 1, \'Disk\', \'0 Bytes\')', true, true)
					ProfileItem('Matrix Assembly', 0, 0, 1, 0, 329188, 'I(3, 2, \'Tetrahedra\', 12806, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'0 Bytes\')', true, true)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Direct Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 1, 0, 4, 0, 659144, 'I(5, 1, \'Type\', \'DCS\', 2, \'Cores\', 4, false, 2, \'Matrix size\', 82953, false, 3, \'Matrix bandwidth\', 20.2207, \'%5.1f\', 1, \'Disk\', \'326 KB\')', true, true)
					ProfileItem('Field Recovery', 0, 0, 0, 0, 659144, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'1.17 MB\')', true, true)
					$begin 'ProfileGroup'
						MajorVer=2025
						MinorVer=2
						Name='APIPms1'
						$begin 'StartInfo'
							I(1, 'Timesinceepock', '1776114230')
						$end 'StartInfo'
						$begin 'TotalInfo'
							I(0, ' ')
						$end 'TotalInfo'
						GroupOptions=16
						TaskDataOptions('CPU Time'=8, 'Real Time'=8)
						ProfileItem('solverinfo', 0, 0, 0, 0, 0, 'I(10, 1, \'Solvertype\', \'shared_memory\', 1, \'Precision\', \'double\', 1, \'Solversymmetry\', \'complex_sym\', 1, \'Matrixdim\', \'82953\', 1, \'Matrixbw\', \'20.227900\', 1, \'Matrixnnz\', \'1677967\', 1, \'Rootdim\', \'611\', 1, \'Mathtype\', \'mkl\', 1, \'Mpitasks\', \'1\', 1, \'Threadspertasks\', \'0\')', false, true)
						ProfileItem('sysinfo', 0, 0, 0, 0, 0, 'I(12, 1, \'Os\', \'win\', 1, \'Cpuid\', \'12th Gen Intel(R) Core(TM) i7-12800H\', 1, \'CpuPhysicCores\', \'14\', 1, \'CpuLogicCores\', \'20\', 1, \'Cpufreqkhz\', \'28722300928.000000\', 1, \'Cpucachelinesizebytes\', \'64\', 1, \'Cpuestlastlevelcachesizemb\', \'25.000000\', 1, \'Cpuestgflops\', \'409.600006\', 1, \'Memorybwestkbps\', \'76.800003\', 1, \'Numanodes\', \'1\', 1, \'Virtualmemkb\', \'137439000000.000000\', 1, \'Pagesizekb\', \'4096\')', false, true)
						ProfileItem('analysisinfo', 0, 0, 0, 0, 0, 'I(9, 1, \'Analysisstatus\', \'valid\', 1, \'Numsupernodes\', \'7282\', 1, \'Factornnz\', \'18096523\', 1, \'Factorestflops\', \'20137400000\', 1, \'Fbsestflops\', \'60091858\', 1, \'Rootfactestflops\', \'76036219\', 1, \'Rootfbsestflops\', \'186660\', 1, \'Analysistimesec\', \'0.671392\', 1, \'Analysismemkb\', \'47980.000000\')', false, true)
						ProfileItem('factorinfo', 0, 0, 0, 0, 0, 'I(4, 1, \'Fatorizationstatus\', \'valid\', 1, \'Factorizationnumcores\', \'4\', 1, \'Factorizationtimesec\', \'0.922906\', 1, \'Factorizationmentotalkb\', \'442683.000000\')', false, true)
						ProfileItem('fbsinfo', 0, 0, 0, 0, 0, 'I(10, 1, \'Fbstatus\', \'valid\', 1, \'Fbstype\', \'fullsolve\', 1, \'Fbsmt\', \'false\', 1, \'Fbsmrhs\', \'false\', 1, \'Fbsnumcores\', \'4\', 1, \'Fbsnumsolvestotal\', \'1\', 1, \'Fbsnumsolves\', \'1\', 1, \'Fbsavgsolvetime1solvesec\', \'0.075864\', 1, \'Fbscputimesec\', \'0.075864\', 1, \'Fbsmemorytotalkb\', \'372932.000000\')', false, true)
						ProfileItem('solverprofile', 0, 0, 0, 0, 0, 'I(2, 1, \'Maxmemkb\', \'442683\', 1, \'Maxdiskkb\', \'0\')', false, true)
					$end 'ProfileGroup'
				$end 'ProfileGroup'
				ProfileItem('Data Transfer', 0, 0, 0, 0, 109668, 'I(1, 0, \'Adaptive Pass 3\')', true, true)
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
				ProfileFootnote('I(1, 3, \'Max Mag. Delta S\', 0.349637, \'%.5f\')', 0)
			$end 'ProfileGroup'
			$begin 'ProfileGroup'
				MajorVer=2025
				MinorVer=2
				Name='Adaptive Pass 4'
				$begin 'StartInfo'
					I(0, 'Adaptive Meshing')
				$end 'StartInfo'
				$begin 'TotalInfo'
					I(0, ' ')
				$end 'TotalInfo'
				GroupOptions=0
				TaskDataOptions('CPU Time'=8, Memory=8, 'Real Time'=8)
				ProfileItem('Adaptive Refine', 1, 0, 1, 0, 48792, 'I(1, 2, \'Tetrahedra\', 20233, false)', true, true)
				ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2.5GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:03')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 219596, 'I(2, 2, \'Tetrahedra\', 16546, false, 1, \'Disk\', \'0 Bytes\')', true, true)
					ProfileItem('Matrix Assembly', 0, 0, 1, 0, 373716, 'I(3, 2, \'Tetrahedra\', 16546, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'0 Bytes\')', true, true)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 1, 0, 3, 0, 497308, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 4, false, 2, \'Matrix size\', 106767, false, 3, \'Matrix bandwidth\', 20.5657, \'%5.1f\', 3, \'Iterations\', 25, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, true)
					ProfileItem('Field Recovery', 0, 0, 0, 0, 497308, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'1.3 MB\')', true, true)
				$end 'ProfileGroup'
				ProfileItem('Data Transfer', 0, 0, 0, 0, 109732, 'I(1, 0, \'Adaptive Pass 4\')', true, true)
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
				ProfileFootnote('I(1, 3, \'Max Mag. Delta S\', 0.0811445, \'%.5f\')', 0)
			$end 'ProfileGroup'
			$begin 'ProfileGroup'
				MajorVer=2025
				MinorVer=2
				Name='Adaptive Pass 5'
				$begin 'StartInfo'
					I(0, 'Adaptive Meshing')
				$end 'StartInfo'
				$begin 'TotalInfo'
					I(0, 'AnsysEDT profile')
				$end 'TotalInfo'
				GroupOptions=0
				TaskDataOptions('CPU Time'=8, Memory=8, 'Real Time'=8)
				ProfileItem('Adaptive Refine', 1, 0, 1, 0, 58152, 'I(1, 2, \'Tetrahedra\', 26637, false)', true, true)
				ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2.5GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:04')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 234140, 'I(2, 2, \'Tetrahedra\', 22637, false, 1, \'Disk\', \'0 Bytes\')', true, true)
					ProfileItem('Matrix Assembly', 0, 0, 2, 0, 443040, 'I(3, 2, \'Tetrahedra\', 22637, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'28 Bytes\')', true, true)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 2, 0, 4, 0, 593308, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 4, false, 2, \'Matrix size\', 145351, false, 3, \'Matrix bandwidth\', 20.8936, \'%5.1f\', 3, \'Iterations\', 28, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, true)
					ProfileItem('Field Recovery', 0, 0, 0, 0, 593308, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'1.87 MB\')', true, true)
				$end 'ProfileGroup'
				ProfileItem('Data Transfer', 0, 0, 0, 0, 109652, 'I(1, 0, \'Adaptive Pass 5\')', true, true)
				ProfileFootnote('I(1, 3, \'Max Mag. Delta S\', 0.017434, \'%.5f\')', 0)
			$end 'ProfileGroup'
			ProfileFootnote('I(1, 0, \'Adaptive Passes converged\')', 0)
		$end 'ProfileGroup'
		$begin 'ProfileGroup'
			MajorVer=2025
			MinorVer=2
			Name='Frequency Sweep'
			$begin 'StartInfo'
				I(1, 'Time', '04/13/2026 17:04:08')
			$end 'StartInfo'
			$begin 'TotalInfo'
				I(1, 'Elapsed Time', '00:01:39')
			$end 'TotalInfo'
			GroupOptions=4
			TaskDataOptions('CPU Time'=8, Memory=8, 'Real Time'=8)
			ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 1, \'HPC\', \'Enabled\')', false, true)
			$begin 'ProfileGroup'
				MajorVer=2025
				MinorVer=2
				Name='Solution - Sweep'
				$begin 'StartInfo'
					I(0, 'Interpolating HFSS Frequency Sweep, Solving Distributed - up to 4 frequencies in parallel')
					I(1, 'Time', '04/13/2026 17:04:08')
				$end 'StartInfo'
				$begin 'TotalInfo'
					I(1, 'Elapsed Time', '00:01:39')
				$end 'TotalInfo'
				GroupOptions=4
				TaskDataOptions('CPU Time'=8, Memory=8, 'Real Time'=8)
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'From 2GHz to 3GHz, 501 Frequencies\')', false, true)
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Frequency: 2.5GHz has already been solved\')', false, true)
				ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 3GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:11')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Distributed Solve Group #1\')', false, true)
					ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 261724, 'I(2, 2, \'Tetrahedra\', 22637, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Matrix Assembly', 3, 0, 3, 0, 329092, 'I(3, 2, \'Tetrahedra\', 22637, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 4, 0, 3, 0, 590000, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 1, false, 2, \'Matrix size\', 145351, false, 3, \'Matrix bandwidth\', 20.8936, \'%5.1f\', 3, \'Iterations\', 15, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Field Recovery', 1, 0, 0, 0, 590000, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'5.9 MB\')', true, false)
				$end 'ProfileGroup'
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:15')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Distributed Solve Group #1\')', false, true)
					ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 262400, 'I(2, 2, \'Tetrahedra\', 22637, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Matrix Assembly', 3, 0, 2, 0, 329476, 'I(3, 2, \'Tetrahedra\', 22637, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 8, 0, 4, 0, 589872, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 1, false, 2, \'Matrix size\', 145351, false, 3, \'Matrix bandwidth\', 20.8936, \'%5.1f\', 3, \'Iterations\', 24, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Field Recovery', 0, 0, 0, 0, 589872, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'5.9 MB\')', true, false)
				$end 'ProfileGroup'
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2.25GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:15')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Distributed Solve Group #1\')', false, true)
					ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 261212, 'I(2, 2, \'Tetrahedra\', 22637, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Matrix Assembly', 3, 0, 2, 0, 328708, 'I(3, 2, \'Tetrahedra\', 22637, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 9, 0, 5, 0, 589008, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 1, false, 2, \'Matrix size\', 145351, false, 3, \'Matrix bandwidth\', 20.8936, \'%5.1f\', 3, \'Iterations\', 28, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Field Recovery', 1, 0, 0, 0, 589008, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'5.9 MB\')', true, false)
				$end 'ProfileGroup'
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2.75GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:17')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Distributed Solve Group #1\')', false, true)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 260888, 'I(2, 2, \'Tetrahedra\', 22637, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Matrix Assembly', 3, 0, 2, 0, 327832, 'I(3, 2, \'Tetrahedra\', 22637, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 11, 0, 5, 0, 588716, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 1, false, 2, \'Matrix size\', 145351, false, 3, \'Matrix bandwidth\', 20.8936, \'%5.1f\', 3, \'Iterations\', 28, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Field Recovery', 0, 0, 0, 0, 588716, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'5.9 MB\')', true, false)
				$end 'ProfileGroup'
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Basis Element # 1, Frequency: 3GHz; Additional basis points are needed before the interpolation error can be computed.\')', false, true)
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Basis Element # 2, Frequency: 2GHz; Additional basis points are needed before the interpolation error can be computed.\')', false, true)
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Basis Element # 3, Frequency: 2.5GHz; S Matrix Error =  23.392%\')', false, true)
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Basis Element # 4, Frequency: 2.25GHz; S Matrix Error =  35.522%\')', false, true)
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Basis Element # 5, Frequency: 2.75GHz; S Matrix Error =  62.509%\')', false, true)
				ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2.875GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:21')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Distributed Solve Group #2\')', false, true)
					ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 261496, 'I(2, 2, \'Tetrahedra\', 22637, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Matrix Assembly', 5, 0, 3, 0, 328880, 'I(3, 2, \'Tetrahedra\', 22637, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 13, 0, 6, 0, 589804, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 1, false, 2, \'Matrix size\', 145351, false, 3, \'Matrix bandwidth\', 20.8936, \'%5.1f\', 3, \'Iterations\', 24, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Field Recovery', 0, 0, 0, 0, 589804, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'5.9 MB\')', true, false)
				$end 'ProfileGroup'
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2.625GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:22')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Distributed Solve Group #2\')', false, true)
					ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 262888, 'I(2, 2, \'Tetrahedra\', 22637, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Matrix Assembly', 6, 0, 3, 0, 329808, 'I(3, 2, \'Tetrahedra\', 22637, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 14, 0, 7, 0, 590480, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 1, false, 2, \'Matrix size\', 145351, false, 3, \'Matrix bandwidth\', 20.8936, \'%5.1f\', 3, \'Iterations\', 28, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Field Recovery', 1, 0, 0, 0, 590480, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'5.9 MB\')', true, false)
				$end 'ProfileGroup'
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2.375GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:23')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Distributed Solve Group #2\')', false, true)
					ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 261668, 'I(2, 2, \'Tetrahedra\', 22637, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Matrix Assembly', 8, 0, 3, 0, 328612, 'I(3, 2, \'Tetrahedra\', 22637, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 13, 0, 7, 0, 594404, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 1, false, 2, \'Matrix size\', 145351, false, 3, \'Matrix bandwidth\', 20.8936, \'%5.1f\', 3, \'Iterations\', 29, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Field Recovery', 0, 0, 0, 0, 594404, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'5.9 MB\')', true, false)
				$end 'ProfileGroup'
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2.125GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:24')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Distributed Solve Group #2\')', false, true)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 261420, 'I(2, 2, \'Tetrahedra\', 22637, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Matrix Assembly', 9, 0, 4, 0, 328972, 'I(3, 2, \'Tetrahedra\', 22637, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 12, 0, 7, 0, 589048, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 1, false, 2, \'Matrix size\', 145351, false, 3, \'Matrix bandwidth\', 20.8936, \'%5.1f\', 3, \'Iterations\', 26, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Field Recovery', 0, 0, 0, 0, 589048, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'5.9 MB\')', true, false)
				$end 'ProfileGroup'
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Basis Element # 6, Frequency: 2.875GHz; S Matrix Error =  28.084%\')', false, true)
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Basis Element # 7, Frequency: 2.625GHz; S Matrix Error =   2.165%\')', false, true)
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Basis Element # 8, Frequency: 2.375GHz; S Matrix Error =   5.586%\')', false, true)
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Basis Element # 9, Frequency: 2.125GHz; S Matrix Error =   4.746%\')', false, true)
				ProfileItem('Data Transfer', 0, 0, 0, 0, 111988, 'I(1, 0, \'Frequency Group #2; Interpolating frequency sweep\')', true, true)
				ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2.3125GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:21')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Distributed Solve Group #3\')', false, true)
					ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 261364, 'I(2, 2, \'Tetrahedra\', 22637, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Matrix Assembly', 4, 0, 3, 0, 328640, 'I(3, 2, \'Tetrahedra\', 22637, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 13, 0, 8, 0, 591832, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 1, false, 2, \'Matrix size\', 145351, false, 3, \'Matrix bandwidth\', 20.8936, \'%5.1f\', 3, \'Iterations\', 28, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Field Recovery', 1, 0, 0, 0, 591832, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'5.9 MB\')', true, false)
				$end 'ProfileGroup'
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2.4375GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:23')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Distributed Solve Group #3\')', false, true)
					ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 262576, 'I(2, 2, \'Tetrahedra\', 22637, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Matrix Assembly', 5, 0, 3, 0, 329564, 'I(3, 2, \'Tetrahedra\', 22637, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 15, 0, 7, 0, 602204, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 1, false, 2, \'Matrix size\', 145351, false, 3, \'Matrix bandwidth\', 20.8936, \'%5.1f\', 3, \'Iterations\', 29, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Field Recovery', 1, 0, 0, 0, 602204, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'5.9 MB\')', true, false)
				$end 'ProfileGroup'
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2.1875GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:24')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Distributed Solve Group #3\')', false, true)
					ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 261756, 'I(2, 2, \'Tetrahedra\', 22637, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Matrix Assembly', 6, 0, 4, 0, 329148, 'I(3, 2, \'Tetrahedra\', 22637, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 15, 0, 7, 0, 589380, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 1, false, 2, \'Matrix size\', 145351, false, 3, \'Matrix bandwidth\', 20.8936, \'%5.1f\', 3, \'Iterations\', 27, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Field Recovery', 0, 0, 0, 0, 589380, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'5.9 MB\')', true, false)
				$end 'ProfileGroup'
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2.0625GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:24')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Distributed Solve Group #3\')', false, true)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 261492, 'I(2, 2, \'Tetrahedra\', 22637, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Matrix Assembly', 7, 0, 4, 0, 328940, 'I(3, 2, \'Tetrahedra\', 22637, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 14, 0, 7, 0, 588840, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 1, false, 2, \'Matrix size\', 145351, false, 3, \'Matrix bandwidth\', 20.8936, \'%5.1f\', 3, \'Iterations\', 25, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Field Recovery', 0, 0, 0, 0, 588840, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'5.9 MB\')', true, false)
				$end 'ProfileGroup'
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Basis Element # 10, Frequency: 2.3125GHz; S Matrix Error =   0.747%\')', false, true)
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Basis Element # 11, Frequency: 2.4375GHz; Scattering matrix quantities converged; Passive within tolerance\')', false, true)
				ProfileItem('Data Transfer', 0, 0, 0, 0, 112056, 'I(1, 0, \'Frequency Group #3; Interpolating frequency sweep\')', true, true)
				ProfileFootnote('I(1, 0, \'Interpolating sweep converged and is passive\')', 0)
				ProfileFootnote('I(1, 0, \'HFSS: Distributed Interpolating sweep\')', 0)
			$end 'ProfileGroup'
		$end 'ProfileGroup'
		ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
		$begin 'ProfileGroup'
			MajorVer=2025
			MinorVer=2
			Name='Simulation Summary'
			$begin 'StartInfo'
			$end 'StartInfo'
			$begin 'TotalInfo'
				I(0, ' ')
			$end 'TotalInfo'
			GroupOptions=0
			TaskDataOptions('CPU Time'=8, Memory=8, 'Real Time'=8)
			ProfileItem('Design Validation', 0, 0, 0, 0, 0, 'I(2, 1, \'Elapsed Time\', \'00:00:00\', 1, \'Total Memory\', \'104 MB\')', false, true)
			ProfileItem('Initial Meshing', 0, 0, 0, 0, 0, 'I(2, 1, \'Elapsed Time\', \'00:00:06\', 1, \'Total Memory\', \'270 MB\')', false, true)
			ProfileItem('Adaptive Meshing', 0, 0, 0, 0, 0, 'I(5, 1, \'Elapsed Time\', \'00:00:34\', 1, \'Average memory/process\', \'579 MB\', 1, \'Max memory/process\', \'579 MB\', 2, \'Max number of processes/frequency\', 1, false, 2, \'Total number of cores\', 4, false)', false, true)
			ProfileItem('Frequency Sweep', 0, 0, 0, 0, 0, 'I(5, 1, \'Elapsed Time\', \'00:01:39\', 1, \'Average memory/process\', \'577 MB\', 1, \'Max memory/process\', \'588 MB\', 2, \'Max number of processes/frequency\', 1, false, 2, \'Total number of cores\', 4, false)', false, true)
			ProfileFootnote('I(3, 2, \'Max solved tets\', 22637, false, 2, \'Max matrix size\', 145351, false, 1, \'Matrix bandwidth\', \'20.9\')', 0)
		$end 'ProfileGroup'
		ProfileFootnote('I(2, 1, \'Stop Time\', \'04/13/2026 17:05:48\', 1, \'Status\', \'Normal Completion\')', 0)
	$end 'ProfileGroup'
$end 'Profile'
