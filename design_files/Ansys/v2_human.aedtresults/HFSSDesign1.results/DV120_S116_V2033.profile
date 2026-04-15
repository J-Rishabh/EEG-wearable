$begin 'Profile'
	$begin 'ProfileGroup'
		MajorVer=2025
		MinorVer=2
		Name='Solution Process'
		$begin 'StartInfo'
			I(1, 'Start Time', '04/13/2026 19:03:01')
			I(1, 'Host', 'RISHABHLAPTOP')
			I(1, 'Processor', '20')
			I(1, 'OS', 'NT 10.0')
			I(1, 'Product', 'HFSS Version 2025.2.0')
		$end 'StartInfo'
		$begin 'TotalInfo'
			I(1, 'Elapsed Time', '00:00:50')
			I(1, 'ComEngine Memory', '142 M')
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
			ProfileItem('Machine', 0, 0, 0, 0, 0, 'I(5, 1, \'Name\', \'RishabhLaptop\', 1, \'Memory\', \'31.7 GB\', 3, \'RAM Limit\', 90, \'%f%%\', 2, \'Cores\', 4, false, 1, \'Free Disk Space\', \'101 GB\')', false, true)
		$end 'ProfileGroup'
		ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 1, \'Allow off core\', \'True\')', false, true)
		ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 1, \'Solution Basis Order\', \'1\')', false, true)
		ProfileItem('Design Validation', 0, 0, 0, 0, 0, 'I(1, 0, \'Elapsed time : 00:00:00 , HFSS ComEngine Memory : 120 M\')', false, true)
		ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Perform full validations with standard port validations\')', false, true)
		ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'\')', false, true)
		$begin 'ProfileGroup'
			MajorVer=2025
			MinorVer=2
			Name='Initial Meshing'
			$begin 'StartInfo'
				I(1, 'Time', '04/13/2026 19:03:02')
			$end 'StartInfo'
			$begin 'TotalInfo'
				I(0, 'AnsysEDT profile')
			$end 'TotalInfo'
			GroupOptions=4
			TaskDataOptions('CPU Time'=8, Memory=8, 'Real Time'=8)
			ProfileItem('Stitch', 2, 0, 2, 0, 106300, 'I(1, 2, \'Triangles\', 2024, false)', true, true)
			ProfileItem('Mesh', 15, 0, 14, 0, 106300, 'I(2, 1, \'Type\', \'Classic\', 2, \'Tetrahedra\', 8458, false)', true, true)
			ProfileItem('Post', 1, 0, 1, 0, 106300, 'I(1, 2, \'Tetrahedra\', 11177, false)', true, true)
			ProfileItem('Manual Refine', 0, 0, 0, 0, 32980, 'I(2, 2, \'Tetrahedra\', 11171, false, 0, \'Male_Head1_Length1\')', true, true)
			ProfileItem('Lambda Refine', 23, 0, 22, 0, 164332, 'I(1, 2, \'Tetrahedra\', 92129, false)', true, true)
			ProfileItem('Simulation Setup', 1, 0, 1, 0, 377248, 'I(2, 2, \'Tetrahedra\', 88640, false, 1, \'Disk\', \'0 Bytes\')', true, true)
			ProfileItem('Port Adapt', 0, 0, 0, 0, 380936, 'I(2, 2, \'Tetrahedra\', 88640, false, 1, \'Disk\', \'33.9 KB\')', true, true)
			ProfileItem('Port Refine', 2, 0, 2, 0, 100636, 'I(1, 2, \'Tetrahedra\', 92176, false)', true, true)
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
				ProfileItem('Design Validation', 0, 0, 0, 0, 0, 'I(2, 1, \'Elapsed Time\', \'00:00:00\', 1, \'Total Memory\', \'120 MB\')', false, true)
			$end 'ProfileGroup'
			ProfileItem('', 0, 0, 0, 0, 0, 'I(1, 0, \'Setup1: Mesh size (94641 volume elements) is greater than the limit for simulation in Ansys Electronics Desktop Student.\')', false, true)
		$end 'ProfileGroup'
		ProfileFootnote('I(2, 1, \'Stop Time\', \'04/13/2026 19:03:51\', 1, \'Status\', \'Engine Detected Error\')', 2)
	$end 'ProfileGroup'
$end 'Profile'
