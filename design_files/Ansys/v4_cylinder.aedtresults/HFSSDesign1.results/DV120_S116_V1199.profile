$begin 'Profile'
	$begin 'ProfileGroup'
		MajorVer=2025
		MinorVer=2
		Name='Solution Process'
		$begin 'StartInfo'
			I(1, 'Start Time', '04/13/2026 22:16:40')
			I(1, 'Host', 'RISHABHLAPTOP')
			I(1, 'Processor', '20')
			I(1, 'OS', 'NT 10.0')
			I(1, 'Product', 'HFSS Version 2025.2.0')
		$end 'StartInfo'
		$begin 'TotalInfo'
			I(1, 'Elapsed Time', '00:03:23')
			I(1, 'ComEngine Memory', '116 M')
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
			ProfileItem('Machine', 0, 0, 0, 0, 0, 'I(5, 1, \'Name\', \'RishabhLaptop\', 1, \'Memory\', \'31.7 GB\', 3, \'RAM Limit\', 90, \'%f%%\', 2, \'Cores\', 4, false, 1, \'Free Disk Space\', \'100 GB\')', false, true)
		$end 'ProfileGroup'
		ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 1, \'Allow off core\', \'True\')', false, true)
		ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 1, \'Solution Basis Order\', \'1\')', false, true)
		ProfileItem('Design Validation', 0, 0, 0, 0, 0, 'I(1, 0, \'Elapsed time : 00:00:00 , HFSS ComEngine Memory : 101 M\')', false, true)
		ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Perform full validations with standard port validations\')', false, true)
		ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
		$begin 'ProfileGroup'
			MajorVer=2025
			MinorVer=2
			Name='Initial Meshing'
			$begin 'StartInfo'
				I(1, 'Time', '04/13/2026 22:16:40')
			$end 'StartInfo'
			$begin 'TotalInfo'
				I(1, 'Elapsed Time', '00:00:14')
			$end 'TotalInfo'
			GroupOptions=4
			TaskDataOptions('CPU Time'=8, Memory=8, 'Real Time'=8)
			ProfileItem('Mesh', 4, 0, 3, 0, 69000, 'I(3, 1, \'Type\', \'TAU\', 2, \'Cores\', 4, false, 0, \'TAU mesher failed. Using backup method\')', true, true)
			ProfileItem('Mesh', 6, 0, 4, 0, 84460, 'I(3, 1, \'Type\', \'Phi\', 2, \'Tetrahedra\', 7171, false, 0, \'Includes 0 sec to protect layer data\')', true, true)
			ProfileItem('Post', 1, 0, 1, 0, 95228, 'I(1, 2, \'Tetrahedra\', 9560, false)', true, true)
			ProfileItem('Manual Refine', 0, 0, 0, 0, 31752, 'I(2, 2, \'Tetrahedra\', 9598, false, 0, \'Length1\')', true, true)
			ProfileItem('Lambda Refine', 3, 0, 3, 0, 68048, 'I(1, 2, \'Tetrahedra\', 31041, false)', true, true)
			ProfileItem('Simulation Setup', 0, 0, 0, 0, 239212, 'I(2, 2, \'Tetrahedra\', 27962, false, 1, \'Disk\', \'0 Bytes\')', true, true)
			ProfileItem('Port Adapt', 0, 0, 0, 0, 248584, 'I(2, 2, \'Tetrahedra\', 27962, false, 1, \'Disk\', \'34.6 KB\')', true, true)
			ProfileItem('Port Refine', 0, 0, 1, 0, 48328, 'I(1, 2, \'Tetrahedra\', 31106, false)', true, true)
		$end 'ProfileGroup'
		$begin 'ProfileGroup'
			MajorVer=2025
			MinorVer=2
			Name='Adaptive Meshing'
			$begin 'StartInfo'
				I(1, 'Time', '04/13/2026 22:16:55')
			$end 'StartInfo'
			$begin 'TotalInfo'
				I(1, 'Elapsed Time', '00:01:40')
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
						I(0, 'Elapsed time : 00:00:10')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 248980, 'I(2, 2, \'Tetrahedra\', 28027, false, 1, \'Disk\', \'0 Bytes\')', true, true)
					ProfileItem('Matrix Assembly', 1, 0, 4, 0, 514404, 'I(3, 2, \'Tetrahedra\', 28027, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'0 Bytes\')', true, true)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 7, 0, 11, 0, 748596, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 4, false, 2, \'Matrix size\', 180871, false, 3, \'Matrix bandwidth\', 21.5842, \'%5.1f\', 3, \'Iterations\', 37, \'%5.1f\', 1, \'Disk\', \'842 Bytes\')', true, true)
					ProfileItem('Field Recovery', 0, 0, 1, 0, 748596, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'5.32 MB\')', true, true)
				$end 'ProfileGroup'
				ProfileItem('Data Transfer', 0, 0, 0, 0, 106616, 'I(1, 0, \'Adaptive Pass 1\')', true, true)
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
				ProfileItem('Adaptive Refine', 2, 0, 3, 0, 81732, 'I(1, 2, \'Tetrahedra\', 45063, false)', true, true)
				ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2.5GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:19')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 278048, 'I(2, 2, \'Tetrahedra\', 40834, false, 1, \'Disk\', \'0 Bytes\')', true, true)
					ProfileItem('Matrix Assembly', 2, 0, 6, 0, 665096, 'I(3, 2, \'Tetrahedra\', 40834, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'34 Bytes\')', true, true)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 15, 0, 24, 0, 960376, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 4, false, 2, \'Matrix size\', 263039, false, 3, \'Matrix bandwidth\', 21.5863, \'%5.1f\', 3, \'Iterations\', 80, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, true)
					ProfileItem('Field Recovery', 0, 0, 1, 0, 960376, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'3.6 MB\')', true, true)
				$end 'ProfileGroup'
				ProfileItem('Data Transfer', 0, 0, 0, 0, 106456, 'I(1, 0, \'Adaptive Pass 2\')', true, true)
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
				ProfileFootnote('I(1, 3, \'Max Mag. Delta S\', 0.881349, \'%.5f\')', 0)
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
				ProfileItem('Adaptive Refine', 1, 0, 1, 0, 71964, 'I(1, 2, \'Tetrahedra\', 49006, false)', true, true)
				ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2.5GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:18')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 284940, 'I(2, 2, \'Tetrahedra\', 44610, false, 1, \'Disk\', \'0 Bytes\')', true, true)
					ProfileItem('Matrix Assembly', 2, 0, 6, 0, 704780, 'I(3, 2, \'Tetrahedra\', 44610, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'0 Bytes\')', true, true)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 13, 0, 24, 0, 1028280, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 4, false, 2, \'Matrix size\', 286963, false, 3, \'Matrix bandwidth\', 21.607, \'%5.1f\', 3, \'Iterations\', 53, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, true)
					ProfileItem('Field Recovery', 0, 0, 1, 0, 1028280, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'2.41 MB\')', true, true)
				$end 'ProfileGroup'
				ProfileItem('Data Transfer', 0, 0, 0, 0, 106684, 'I(1, 0, \'Adaptive Pass 3\')', true, true)
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
				ProfileFootnote('I(1, 3, \'Max Mag. Delta S\', 0.265396, \'%.5f\')', 0)
			$end 'ProfileGroup'
			$begin 'ProfileGroup'
				MajorVer=2025
				MinorVer=2
				Name='Adaptive Pass 4'
				$begin 'StartInfo'
					I(0, 'Adaptive Meshing')
				$end 'StartInfo'
				$begin 'TotalInfo'
					I(0, 'AnsysEDT profile')
				$end 'TotalInfo'
				GroupOptions=0
				TaskDataOptions('CPU Time'=8, Memory=8, 'Real Time'=8)
				ProfileItem('Adaptive Refine', 1, 0, 1, 0, 74516, 'I(1, 2, \'Tetrahedra\', 53743, false)', true, true)
				ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2.5GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:15')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 296432, 'I(2, 2, \'Tetrahedra\', 49166, false, 1, \'Disk\', \'0 Bytes\')', true, true)
					ProfileItem('Matrix Assembly', 2, 0, 7, 0, 758848, 'I(3, 2, \'Tetrahedra\', 49166, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'28 Bytes\')', true, true)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 11, 0, 20, 0, 1131356, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 4, false, 2, \'Matrix size\', 315827, false, 3, \'Matrix bandwidth\', 21.6384, \'%5.1f\', 3, \'Iterations\', 48, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, true)
					ProfileItem('Field Recovery', 0, 0, 1, 0, 1131356, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'2.7 MB\')', true, true)
				$end 'ProfileGroup'
				ProfileItem('Data Transfer', 0, 0, 0, 0, 106468, 'I(1, 0, \'Adaptive Pass 4\')', true, true)
				ProfileFootnote('I(1, 3, \'Max Mag. Delta S\', 0.00239746, \'%.5f\')', 0)
			$end 'ProfileGroup'
			ProfileFootnote('I(1, 0, \'Adaptive Passes converged\')', 0)
		$end 'ProfileGroup'
		$begin 'ProfileGroup'
			MajorVer=2025
			MinorVer=2
			Name='Frequency Sweep'
			$begin 'StartInfo'
				I(1, 'Time', '04/13/2026 22:18:35')
			$end 'StartInfo'
			$begin 'TotalInfo'
				I(1, 'Elapsed Time', '00:01:28')
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
					I(1, 'Time', '04/13/2026 22:18:35')
				$end 'StartInfo'
				$begin 'TotalInfo'
					I(1, 'Elapsed Time', '00:01:27')
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
						I(0, 'Elapsed time : 00:00:37')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Distributed Solve Group #1\')', false, true)
					ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 322444, 'I(2, 2, \'Tetrahedra\', 49166, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Matrix Assembly', 6, 0, 6, 0, 465204, 'I(3, 2, \'Tetrahedra\', 49166, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 28, 0, 27, 0, 1103964, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 1, false, 2, \'Matrix size\', 315827, false, 3, \'Matrix bandwidth\', 21.6384, \'%5.1f\', 3, \'Iterations\', 69, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Field Recovery', 0, 0, 0, 0, 1103964, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'12.7 MB\')', true, false)
				$end 'ProfileGroup'
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:28')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Distributed Solve Group #1\')', false, true)
					ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 324012, 'I(2, 2, \'Tetrahedra\', 49166, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Matrix Assembly', 6, 0, 6, 0, 467500, 'I(3, 2, \'Tetrahedra\', 49166, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 19, 0, 18, 0, 1020040, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 1, false, 2, \'Matrix size\', 315827, false, 3, \'Matrix bandwidth\', 21.6384, \'%5.1f\', 3, \'Iterations\', 36, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Field Recovery', 1, 0, 1, 0, 1020040, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'12.7 MB\')', true, false)
				$end 'ProfileGroup'
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2.25GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:30')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Distributed Solve Group #1\')', false, true)
					ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
					ProfileItem('Simulation Setup ', 1, 0, 1, 0, 321268, 'I(2, 2, \'Tetrahedra\', 49166, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Matrix Assembly', 6, 0, 5, 0, 464716, 'I(3, 2, \'Tetrahedra\', 49166, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'4 Bytes\')', true, false)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 21, 0, 21, 0, 1076104, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 1, false, 2, \'Matrix size\', 315827, false, 3, \'Matrix bandwidth\', 21.6384, \'%5.1f\', 3, \'Iterations\', 42, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Field Recovery', 1, 0, 1, 0, 1076104, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'12.7 MB\')', true, false)
				$end 'ProfileGroup'
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2.75GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:34')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Distributed Solve Group #1\')', false, true)
					ProfileItem('Simulation Setup ', 1, 0, 1, 0, 321040, 'I(2, 2, \'Tetrahedra\', 49166, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Matrix Assembly', 5, 0, 5, 0, 464904, 'I(3, 2, \'Tetrahedra\', 49166, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 25, 0, 24, 0, 1080392, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 1, false, 2, \'Matrix size\', 315827, false, 3, \'Matrix bandwidth\', 21.6384, \'%5.1f\', 3, \'Iterations\', 54, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Field Recovery', 0, 0, 0, 0, 1080392, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'12.7 MB\')', true, false)
				$end 'ProfileGroup'
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Basis Element # 1, Frequency: 3GHz; Additional basis points are needed before the interpolation error can be computed.\')', false, true)
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Basis Element # 2, Frequency: 2GHz; Additional basis points are needed before the interpolation error can be computed.\')', false, true)
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Basis Element # 3, Frequency: 2.5GHz; S Matrix Error = 210.167%\')', false, true)
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Basis Element # 4, Frequency: 2.25GHz; S Matrix Error =  10.340%\')', false, true)
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Basis Element # 5, Frequency: 2.75GHz; S Matrix Error =   0.639%\')', false, true)
				ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2.875GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:42')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Distributed Solve Group #2\')', false, true)
					ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 321524, 'I(2, 2, \'Tetrahedra\', 49166, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Matrix Assembly', 9, 0, 9, 0, 466112, 'I(3, 2, \'Tetrahedra\', 49166, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 30, 0, 29, 0, 1081520, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 1, false, 2, \'Matrix size\', 315827, false, 3, \'Matrix bandwidth\', 21.6384, \'%5.1f\', 3, \'Iterations\', 60, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Field Recovery', 0, 0, 0, 0, 1081520, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'12.7 MB\')', true, false)
				$end 'ProfileGroup'
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2.625GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:39')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Distributed Solve Group #2\')', false, true)
					ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 323072, 'I(2, 2, \'Tetrahedra\', 49166, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Matrix Assembly', 9, 0, 9, 0, 466540, 'I(3, 2, \'Tetrahedra\', 49166, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 27, 0, 26, 0, 1093808, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 1, false, 2, \'Matrix size\', 315827, false, 3, \'Matrix bandwidth\', 21.6384, \'%5.1f\', 3, \'Iterations\', 51, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Field Recovery', 1, 0, 1, 0, 1093808, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'12.7 MB\')', true, false)
				$end 'ProfileGroup'
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2.375GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:39')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Distributed Solve Group #2\')', false, true)
					ProfileItem(' ', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
					ProfileItem('Simulation Setup ', 0, 0, 0, 0, 321228, 'I(2, 2, \'Tetrahedra\', 49166, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Matrix Assembly', 9, 0, 9, 0, 464564, 'I(3, 2, \'Tetrahedra\', 49166, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 26, 0, 25, 0, 1096520, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 1, false, 2, \'Matrix size\', 315827, false, 3, \'Matrix bandwidth\', 21.6384, \'%5.1f\', 3, \'Iterations\', 45, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Field Recovery', 1, 0, 1, 0, 1096520, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'12.7 MB\')', true, false)
				$end 'ProfileGroup'
				$begin 'ProfileGroup'
					MajorVer=2025
					MinorVer=2
					Name='Frequency - 2.125GHz'
					$begin 'StartInfo'
						I(0, 'RishabhLaptop')
					$end 'StartInfo'
					$begin 'TotalInfo'
						I(0, 'Elapsed time : 00:00:37')
					$end 'TotalInfo'
					GroupOptions=0
					TaskDataOptions('CPU Time'=8, 'Real Time'=8)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Distributed Solve Group #2\')', false, true)
					ProfileItem('Simulation Setup ', 1, 0, 1, 0, 320788, 'I(2, 2, \'Tetrahedra\', 49166, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Matrix Assembly', 9, 0, 9, 0, 465420, 'I(3, 2, \'Tetrahedra\', 49166, false, 2, \'Lumped ports\', 1, false, 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Matrix solver automatically selects Iterative Solver\\\
\')', false, true)
					ProfileItem('Matrix Solve', 23, 0, 23, 0, 1035604, 'I(6, 1, \'Type\', \'DCS-L2\', 2, \'Cores\', 1, false, 2, \'Matrix size\', 315827, false, 3, \'Matrix bandwidth\', 21.6384, \'%5.1f\', 3, \'Iterations\', 39, \'%5.1f\', 1, \'Disk\', \'0 Bytes\')', true, false)
					ProfileItem('Field Recovery', 1, 0, 1, 0, 1035604, 'I(2, 2, \'Excitations\', 1, false, 1, \'Disk\', \'12.7 MB\')', true, false)
				$end 'ProfileGroup'
				ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Basis Element # 6, Frequency: 2.875GHz; Scattering matrix quantities converged; Passive within tolerance\')', false, true)
				ProfileItem('Data Transfer', 0, 0, 0, 0, 108908, 'I(1, 0, \'Frequency Group #2; Interpolating frequency sweep\')', true, true)
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
			ProfileItem('Design Validation', 0, 0, 0, 0, 0, 'I(2, 1, \'Elapsed Time\', \'00:00:00\', 1, \'Total Memory\', \'101 MB\')', false, true)
			ProfileItem('Initial Meshing', 0, 0, 0, 0, 0, 'I(2, 1, \'Elapsed Time\', \'00:00:14\', 1, \'Total Memory\', \'336 MB\')', false, true)
			ProfileItem('Adaptive Meshing', 0, 0, 0, 0, 0, 'I(5, 1, \'Elapsed Time\', \'00:01:40\', 1, \'Average memory/process\', \'1.08 GB\', 1, \'Max memory/process\', \'1.08 GB\', 2, \'Max number of processes/frequency\', 1, false, 2, \'Total number of cores\', 4, false)', false, true)
			ProfileItem('Frequency Sweep', 0, 0, 0, 0, 0, 'I(5, 1, \'Elapsed Time\', \'00:01:28\', 1, \'Average memory/process\', \'1.02 GB\', 1, \'Max memory/process\', \'1.05 GB\', 2, \'Max number of processes/frequency\', 1, false, 2, \'Total number of cores\', 4, false)', false, true)
			ProfileFootnote('I(3, 2, \'Max solved tets\', 49166, false, 2, \'Max matrix size\', 315827, false, 1, \'Matrix bandwidth\', \'21.6\')', 0)
		$end 'ProfileGroup'
		ProfileFootnote('I(2, 1, \'Stop Time\', \'04/13/2026 22:20:03\', 1, \'Status\', \'Normal Completion\')', 0)
	$end 'ProfileGroup'
$end 'Profile'
